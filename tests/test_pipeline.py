"""End to end on a synthetic git repository and synthetic transcripts.

Two models write a small notes repository. One of them commits the other's file, one
transcript survives only in a snapshot copy of the home directory, and one edit is too
short to probe. The pipeline has to get each case right.
"""
import json, os, sqlite3, subprocess, sys, tempfile, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from auctor import attribute, check, config, history, tables, transcripts  # noqa: E402

T0 = 1767261600  # 2026-01-01T10:00:00Z

ALPHA = """# Storks

The white stork leaves the river meadows in the last week of August and does not hurry.
It crosses the strait at the narrowest point because it cannot soar over open water at all.
Ringing records from the nineteen thirties already showed two routes dividing along one line.
"""
ALPHA_MORE = """Birds raised east of that line turn toward the Bosphorus without ever having been shown the way.
The return leg in spring is faster, and nobody has explained the difference to general satisfaction.
"""
BETA = """# Eels

Every eel in a European river was born in the Sargasso Sea and nobody has watched it happen there.
The larvae drift for the better part of a year before they reach the shelf and change their shape.
It isn’t known how an adult finds its way back across an ocean it crossed as a transparent leaf.
"""
GAMMA = """# Swifts

A common swift may stay in the air for ten months without landing once between two breeding seasons.
It sleeps on the wing by climbing at dusk to a height where the air is steady enough to glide in.
"""


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def claude_line(session, model, t, tool_id, name, inp):
    return json.dumps({"type": "assistant", "sessionId": session, "timestamp": iso(t), "cwd": "/work/notes",
                       "message": {"model": model, "content": [
                           {"type": "tool_use", "id": tool_id, "name": name, "input": inp}]}})


def codex_exec(t, call_id, cmd):
    source = "const r = await tools.exec_command(" + json.dumps({"cmd": cmd}) + ");\ntext(r.output);\n"
    return json.dumps({"timestamp": iso(t), "type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": call_id, "input": source}})


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        repo, home, snap = (os.path.join(root, d) for d in ("notes", "home", "snapshot"))
        os.makedirs(repo)

        def git(*args, t=None):
            env = dict(os.environ, GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="t@example.invalid",
                       GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="t@example.invalid",
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
            if t:
                env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{t} +0000"
            subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

        def write(name, text):
            with open(os.path.join(repo, name), "w") as f:
                f.write(text)

        def commit(msg, t):
            git("add", "-A")
            git("commit", "-q", "-m", msg, t=t)

        git("init", "-q")
        write("gamma.md", GAMMA);               commit("Add the note on swifts aloft", T0 - 40 * 86400 + 30)
        write("alpha.md", ALPHA);               commit("Add alpha note on the migration of storks", T0 + 62)
        write("beta.md", BETA);                 commit("Add beta note on the spawning of eels", T0 + 7202)
        write("alpha.md", ALPHA + ALPHA_MORE);  commit("Extend alpha with the return leg in spring", T0 + 10852)
        write("beta.md", BETA + "Short fix.\n"); commit("Append a short closing line to beta", T0 + 14402)

        # Claude Code, live: writes alpha, commits alpha, commits beta (not its own), makes the short edit
        d = os.path.join(home, ".claude", "projects", "-work-notes")
        os.makedirs(d)
        with open(os.path.join(d, "s1.jsonl"), "w") as f:
            f.write("\n".join([
                claude_line("s1", "claude-test-1", T0, "toolu_1", "Write", {"file_path": "/work/notes/alpha.md", "content": ALPHA}),
                claude_line("s1", "claude-test-1", T0 + 60, "toolu_2", "Bash", {"command": 'git add -A && git commit -m "Add alpha note on the migration of storks"'}),
                claude_line("s1", "claude-test-1", T0 + 7100, "toolu_3", "Read", {"file_path": "/work/notes/beta.md"}),
                claude_line("s1", "claude-test-1", T0 + 7200, "toolu_4", "Bash", {"command": 'git commit -am "Add beta note on the spawning of eels"'}),
                claude_line("s1", "claude-test-1", T0 + 14390, "toolu_5", "Edit", {"file_path": "/work/notes/beta.md", "old_string": "leaf.\n", "new_string": "leaf.\nShort fix.\n"}),
                claude_line("s1", "claude-test-1", T0 + 14400, "toolu_6", "Bash", {"command": 'git commit -am "Append a short closing line to beta"'}),
            ]) + "\n")

        # Claude Code, an older session that survives only in the snapshot copy of home
        d = os.path.join(snap, ".claude", "projects", "-work-notes")
        os.makedirs(d)
        with open(os.path.join(d, "s0.jsonl"), "w") as f:
            f.write("\n".join([
                claude_line("s0", "claude-old-1", T0 - 40 * 86400, "toolu_0", "Write", {"file_path": "/work/notes/gamma.md", "content": GAMMA}),
                claude_line("s0", "claude-old-1", T0 - 40 * 86400 + 20, "toolu_00", "Bash", {"command": 'git add -A; git commit -m "Add the note on swifts aloft"'}),
            ]) + "\n")

        # Codex: writes beta through a heredoc (escaped inside source text), later extends alpha and commits it
        d = os.path.join(home, ".codex", "sessions", "2026", "01", "01")
        os.makedirs(d)
        with open(os.path.join(d, "rollout-c1.jsonl"), "w") as f:
            f.write("\n".join([
                json.dumps({"timestamp": iso(T0 + 3500), "type": "session_meta", "payload": {"id": "c1", "cwd": "/work/notes"}}),
                json.dumps({"timestamp": iso(T0 + 3501), "type": "turn_context", "payload": {"model": "gpt-test-1", "cwd": "/work/notes"}}),
                codex_exec(T0 + 3600, "call_1", "cat > beta.md <<'E'\n" + BETA + "E"),
                codex_exec(T0 + 10800, "call_2", "cat >> alpha.md <<'E'\n" + ALPHA_MORE + "E"),
                codex_exec(T0 + 10850, "call_3", 'git commit -am "Extend alpha with the return leg in spring"'),
            ]) + "\n")

        cfg_path = os.path.join(root, "auctor.toml")
        with open(cfg_path, "w") as f:
            f.write(f'repo = "notes"\nhome = "home"\nhome_copies = ["{snap}"]\nout = "out"\n')
        cls.cfg = config.load(cfg_path)
        cls.db = sqlite3.connect(cls.cfg.db)
        transcripts.build(cls.cfg, cls.db)
        history.build(cls.cfg, cls.db)
        attribute.build(cls.cfg, cls.db)
        tables.build(cls.cfg, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def rev(self, path, status, nth=0):
        return self.db.execute("select model, basis, commit_model, provider from revision_attr "
                               "where path=? and status=? order by ts", (path, status)).fetchall()[nth]

    def test_detects_git(self):
        self.assertEqual(self.cfg.vcs, "git")

    def test_writer_who_also_commits(self):
        self.assertEqual(self.rev("alpha.md", "A"), ("claude-test-1", "content", "claude-test-1", "anthropic"))

    def test_writer_differs_from_committer(self):
        self.assertEqual(self.rev("beta.md", "A"), ("gpt-test-1", "content", "claude-test-1", "openai"))

    def test_second_model_extends_a_note(self):
        self.assertEqual(self.rev("alpha.md", "M")[:2], ("gpt-test-1", "content"))
        added = self.db.execute("select added_words from revision_attr where path='alpha.md' and status='M'").fetchone()[0]
        self.assertEqual(added, len(ALPHA_MORE.split()))

    def test_short_edit_falls_back_to_committer(self):
        self.assertEqual(self.rev("beta.md", "M")[:2], ("claude-test-1", "commit"))

    def test_transcript_only_in_snapshot(self):
        self.assertEqual(self.rev("gamma.md", "A")[:2], ("claude-old-1", "content"))

    def test_notes_rollup(self):
        creator, n_models, split = self.db.execute(
            "select creator_model, n_models, words_by_model from notes where path='alpha.md'").fetchone()
        self.assertEqual((creator, n_models), ("claude-test-1", 2))
        self.assertEqual(json.loads(split), {"claude-test-1": len(ALPHA.split()), "gpt-test-1": len(ALPHA_MORE.split())})

    def test_reads_are_not_writes(self):
        self.assertEqual(self.db.execute("select session, path from reads").fetchall(), [("s1", "/work/notes/beta.md")])

    def test_rerun_adds_nothing(self):
        n = self.db.execute("select count(*) from events").fetchone()[0]
        transcripts.build(self.cfg, self.db)
        self.assertEqual(self.db.execute("select count(*) from events").fetchone()[0], n)

    def test_check_finds_every_probe_unshared(self):
        r = check.run(self.cfg, self.db)
        self.assertGreater(r["probes"], 0)
        self.assertEqual((r["many_models"], r["unsupported"]), (0, 0))

    def test_exports(self):
        for name in ("revisions.csv", "notes.csv"):
            self.assertTrue(os.path.getsize(os.path.join(self.cfg.out, name)) > 0)


if __name__ == "__main__":
    unittest.main()
