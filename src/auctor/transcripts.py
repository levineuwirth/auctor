"""Extract tool-call inputs from agent transcripts: the only place an agent's text enters a file.

events  one row per tool call that can write (Claude Code: Bash, Write, Edit; Codex: exec)
reads   Claude Code Read calls on paths inside the repository, for an exposure graph

Tool results are never kept, so reading a file cannot count as writing it.

The run is additive. Rows are keyed on the tool-call id and never dropped, so what has been
extracted survives the transcripts it came from. That matters: Claude Code deletes transcripts
after 30 days by default, and older ones survive only in filesystem snapshots (`home_copies`).

A new harness needs one function here that inserts the same `events` rows.
"""
import glob, json, os, re, sys, zlib
from datetime import datetime

WRITE_TOOLS = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}

SCHEMA = """
create table if not exists events (id integer primary key, key text unique, harness text, session text,
    agent_id text, subagent int, ts real, model text, tool text, cwd text, text blob, norm blob);
create table if not exists reads (harness text, session text, ts real, model text, path text,
    unique (session, ts, path));
create index if not exists events_ts on events(ts);
create index if not exists events_session on events(session);
"""


def norm(s):
    """Lowercase letters and digits only. The same sentence reaches a file through heredocs,
    JSON specs and escaped strings; this is what survives all of them."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


_esc = re.compile(r'\\u([0-9a-fA-F]{4})|\\([nt"\\\'`$])')


def unescape(s):
    """Undo one level of string escaping, for commands embedded in source text."""
    def f(m):
        if m.group(1):
            return chr(int(m.group(1), 16))
        return {"n": "\n", "t": "\t"}.get(m.group(2), m.group(2))
    return _esc.sub(f, s)


def sources(cfg, rel):
    """Every transcript under `rel` in the home directory and its copies. A transcript is
    append-only, so only the largest copy of each file is read."""
    best = {}
    for root in [cfg.home] + cfg.home_copies:
        base = os.path.join(root, rel)
        for f in glob.glob(base + "/**/*.jsonl", recursive=True):
            k = os.path.relpath(f, base)
            try:
                size = os.path.getsize(f)
            except OSError:
                continue
            if k not in best or size > best[k][1]:
                best[k] = (f, size)
    return sorted(v[0] for v in best.values())


def _open(path):
    """A transcript can be deleted between listing and reading; a harness cleans up as it runs."""
    try:
        return open(path, errors="replace")
    except OSError:
        return open(os.devnull)


def _insert(db, key, harness, session, agent_id, sub, ts, model, tool, cwd, text):
    db.execute("insert or ignore into events values (null,?,?,?,?,?,?,?,?,?,?,?)",
               (key, harness, session, agent_id, sub, ts, model, tool, cwd,
                zlib.compress(text.encode("utf-8", "replace")), zlib.compress(norm(text).encode())))


def claude_code(cfg, db):
    marker = cfg.marker
    for f in sources(cfg, ".claude/projects"):
        sub = "/subagents/" in f
        with _open(f) as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                m = d.get("message") or {}
                model = m.get("model")
                if not model or model.startswith("<"):
                    continue
                for b in m.get("content") or []:
                    if b.get("type") != "tool_use":
                        continue
                    name, inp, t = b.get("name"), b.get("input") or {}, _ts(d["timestamp"])
                    if name == "Read":
                        p = inp.get("file_path", "")
                        if marker in p:
                            db.execute("insert or ignore into reads values (?,?,?,?,?)",
                                       ("claude-code", d.get("sessionId"), t, model, p))
                        continue
                    if name not in WRITE_TOOLS:
                        continue
                    if name == "Bash":
                        text = inp.get("command", "")
                    elif name == "Write":
                        text = inp.get("file_path", "") + "\n" + inp.get("content", "")
                    elif name == "Edit":  # old_string is someone else's text being quoted
                        text = inp.get("file_path", "") + "\n" + inp.get("new_string", "")
                    else:
                        text = json.dumps(inp, ensure_ascii=False)
                    _insert(db, b.get("id"), "claude-code", d.get("sessionId"), d.get("agentId"),
                            int(sub or bool(d.get("isSidechain"))), t, model, name, d.get("cwd"), text)
        db.commit()


def codex(cfg, db):
    for f in sources(cfg, ".codex/sessions"):
        model = sid = cwd = None
        sub = 0
        with _open(f) as fh:
            for line in fh:
                head = line[:300]
                if '"session_meta"' in head:
                    p = json.loads(line)["payload"]
                    sid, cwd = p.get("id"), p.get("cwd")
                    sub = int(p.get("thread_source") not in (None, "user"))
                elif '"turn_context"' in head:
                    p = json.loads(line)["payload"]
                    model, cwd = p.get("model"), p.get("cwd") or cwd
                elif '"response_item"' in head and ('"custom_tool_call"' in head or '"function_call"' in head):
                    d = json.loads(line)
                    p = d["payload"]
                    if p.get("type") not in ("custom_tool_call", "function_call"):
                        continue
                    if p.get("namespace") in ("collaboration", "clock") or p.get("name") == "wait":
                        continue
                    text = unescape(p.get("input") or p.get("arguments") or "")
                    _insert(db, p.get("call_id") or p.get("id"), "codex", sid, None, sub,
                            _ts(d["timestamp"]), model, p.get("name"), cwd, text)
        db.commit()


EXTRACTORS = {"claude-code": claude_code, "codex": codex}


def build(cfg, db):
    db.executescript(SCHEMA)
    before = db.execute("select count(*) from events").fetchone()[0]
    for name, extract in EXTRACTORS.items():
        extract(cfg, db)
        print(f"transcripts: {name} done", file=sys.stderr)
    after = db.execute("select count(*) from events").fetchone()[0]
    print(f"transcripts: {after} tool calls ({after - before} new)")
