"""Settings, read from a TOML file. Relative paths resolve against the file's directory."""
import glob, os, tomllib
from dataclasses import dataclass

DEFAULT_PROVIDERS = {"claude": "anthropic", "gpt": "openai", "gemini": "google"}


@dataclass
class Config:
    repo: str            # the repository whose history is studied
    vcs: str             # "git" or "levcs"
    home: str            # home directory holding the harness transcripts
    home_copies: list    # other copies of it (filesystem snapshots)
    db: str              # SQLite file everything is written to
    out: str             # directory for the CSV exports
    extensions: tuple    # file suffixes treated as prose
    keys: dict           # levcs: signing-key fingerprint -> name
    providers: dict      # model-id prefix -> provider

    @property
    def marker(self):
        """How a path inside the repository is recognized in a transcript."""
        return os.path.basename(self.repo.rstrip("/"))


def load(path):
    base = os.path.dirname(os.path.abspath(path))
    with open(path, "rb") as f:
        c = tomllib.load(f)
    rel = lambda p: os.path.normpath(os.path.join(base, os.path.expanduser(p)))
    repo = rel(c["repo"])
    vcs = c.get("vcs") or ("levcs" if os.path.isdir(os.path.join(repo, ".levcs")) else "git")
    if vcs not in ("git", "levcs"):
        raise SystemExit(f"vcs must be git or levcs, not {vcs!r}")
    copies = sorted(p for g in c.get("home_copies", []) for p in glob.glob(os.path.expanduser(g)))
    return Config(
        repo=repo, vcs=vcs, home=rel(c.get("home", "~")), home_copies=copies,
        db=rel(c.get("db", "corpus.db")), out=rel(c.get("out", ".")),
        extensions=tuple(c.get("extensions", [".md"])), keys=c.get("keys", {}),
        providers={**DEFAULT_PROVIDERS, **c.get("providers", {})},
    )
