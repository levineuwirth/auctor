"""Read a repository's history into the database. Read-only: log and show, nothing else.

commits(hash, ts, key, message)
revisions(hash, path, status, ts, words, added_words, text, added)
  text  = the file at that revision (zlib)
  added = sentences present at this revision and absent from the previous one (zlib):
          what the revision's author wrote, as distinct from what they inherited.
"""
import re, subprocess, sys, zlib
from collections import Counter

SENT = re.compile(r"(?<=[.!?:;])\s+|\n+")


def sentences(text):
    return [s.strip() for s in SENT.split(text) if s.strip()]


def added(new, old):
    """Sentences of `new` not accounted for by `old`, as a multiset difference, in order.

    Sentences and not lines: a prose paragraph is one line, and a one-word edit would
    otherwise hand the whole paragraph to the editor.
    """
    have, out = Counter(sentences(old)), []
    for s in sentences(new):
        if have[s] > 0:
            have[s] -= 1
        else:
            out.append(s)
    return "\n".join(out)


def _run(repo, *args):
    return subprocess.run(args, cwd=repo, capture_output=True).stdout


class Git:
    def __init__(self, cfg):
        self.repo = cfg.repo

    def commits(self):
        out = _run(self.repo, "git", "-c", "core.quotepath=false", "log", "--reverse", "--no-renames",
                   "--name-status", "--format=%x1e%H%x1f%ct%x1f%an%x1f%B%x1f").decode("utf-8", "replace")
        for rec in out.split("\x1e")[1:]:
            h, ct, name, body, rest = rec.split("\x1f", 4)
            changes = [("M" if l[0] == "T" else l[0], l.split("\t", 1)[1])
                       for l in rest.split("\n") if re.match(r"^[AMDT]\t", l)]
            yield {"hash": h, "ts": float(ct), "key": name, "message": body.strip(), "changes": changes}

    def show(self, h, path):
        return _run(self.repo, "git", "show", f"{h}:{path}")


class Levcs:
    def __init__(self, cfg):
        self.repo, self.keys = cfg.repo, cfg.keys

    def commits(self):
        found, cur = [], None
        for line in _run(self.repo, "levcs", "log", "--name-status").decode("utf-8", "replace").split("\n"):
            if line.startswith("commit "):
                cur = {"hash": line.split()[1], "msg": [], "changes": [], "key": None, "ts": 0.0}
                found.append(cur)
            elif cur is None:
                continue
            elif line.startswith("Author: "):
                fp = line.split(":")[-1].strip()
                cur["key"] = self.keys.get(fp, fp)
            elif line.startswith("Date: "):
                cur["ts"] = int(line.split()[1]) / 1e6
            elif line.startswith("    "):
                cur["msg"].append(line[4:])
            elif re.match(r"^[AMD]\t", line):
                cur["changes"].append((line[0], line[2:]))
        for c in reversed(found):  # the log is newest first
            c["message"] = "\n".join(c.pop("msg")).strip()
            yield c

    def show(self, h, path):
        return _run(self.repo, "levcs", "show", f"{h}:{path}")


BACKENDS = {"git": Git, "levcs": Levcs}


def build(cfg, db):
    vcs = BACKENDS[cfg.vcs](cfg)
    db.executescript("""
    drop table if exists commits; drop table if exists revisions;
    create table commits (hash text primary key, ts real, key text, message text);
    create table revisions (id integer primary key, hash text, path text, status text, ts real,
        words int, added_words int, text blob, added blob);
    """)
    last = {}
    for i, c in enumerate(vcs.commits()):
        db.execute("insert or ignore into commits values (?,?,?,?)", (c["hash"], c["ts"], c["key"], c["message"]))
        for st, path in c["changes"]:
            if not path.endswith(cfg.extensions):
                continue
            if st == "D":
                db.execute("insert into revisions values (null,?,?,?,?,0,0,null,null)", (c["hash"], path, st, c["ts"]))
                last.pop(path, None)
                continue
            text = vcs.show(c["hash"], path).decode("utf-8", "replace")
            new = added(text, last.get(path, ""))
            last[path] = text
            db.execute("insert into revisions values (null,?,?,?,?,?,?,?,?)",
                       (c["hash"], path, st, c["ts"], len(text.split()), len(new.split()),
                        zlib.compress(text.encode()), zlib.compress(new.encode())))
        if i % 200 == 0:
            print(f"history: {i} commits", file=sys.stderr)
            db.commit()
    db.execute("create index rev_path on revisions(path, ts)")
    db.commit()
    n, w = db.execute("select count(*), sum(added_words) from revisions where status != 'D'").fetchone()
    print(f"history: {n} revisions, {w or 0} authored words")
