# auctor

Which model wrote which words in a repository that several coding agents write to.

Version history usually cannot say. Agents commit under one identity, and the session that
commits a file is not always the one that wrote it. But the only way an agent's prose reaches
a file is through a tool call, so the agents' own transcripts hold every sentence at the
moment it was written, stamped with the model that wrote it. `auctor` joins the two.

It reads git or LeVCS history and Claude Code and Codex transcripts. It writes one SQLite file
and two CSVs, and never touches the repository it studies.

## Use

Python 3.11 or later, standard library only.

```bash
cp auctor.example.toml auctor.toml     # set `repo`
PYTHONPATH=src python3 -m auctor all   # or: pip install -e . && auctor all
PYTHONPATH=src python3 -m auctor check
```

`all` runs four steps, each also available alone:

| step | does | on rerun |
|---|---|---|
| `transcripts` | extracts tool-call inputs from `~/.claude/projects` and `~/.codex/sessions` | adds only |
| `history` | reads every revision of every prose file | rebuilds |
| `attribute` | joins the two | rebuilds |
| `tables` | writes `revision_attr` and `notes`, exports `revisions.csv` and `notes.csv` | rebuilds |

**The database and CSVs contain your transcripts' text and your repository's text.** Keep them
out of anything you publish. `auctor.toml`, `*.db` and `*.csv` are ignored here for that reason.

### Transcripts expire

Claude Code deletes transcripts after 30 days unless `cleanupPeriodDays` says otherwise.
`transcripts` never drops a row, so run it at least monthly and the record stays unbroken.
For what is already gone, point `home_copies` at filesystem snapshots of your home directory;
the largest copy of each transcript is read, wherever it is.

## How the join works

For each revision, the sentences present now and absent from the file's previous revision are
what that revision's author wrote. Up to eight of them, spread through the file, each supply
a probe: the middle 40 characters after reduction to lowercase letters and digits. That
reduction is what lets a sentence be recognized whether it traveled through a `Write` call, a
heredoc, a JSON spec or an escaped string.

Each probe is searched for in the tool-call inputs emitted before the commit: first those of
the committing session, then everyone's since the file's previous revision (seven days back
for a new file). The earliest emitter wrote it. The plurality model across probes is the
revision's writer.

Tool results are never indexed, so a session that read a file is not mistaken for one that
wrote it. An `Edit` contributes only its new string; the old one is someone else's text.

A revision with no sentence long enough to probe falls back to the session whose tool call
carried the commit message.

## Output

`revisions.csv`, one row per file revision:

| column | meaning |
|---|---|
| `rev`, `hash`, `ts`, `path`, `status` | the revision; `status` is A, M or D |
| `kind` | top-level directory |
| `words`, `added_words` | file length at this revision; words in sentences new to it |
| `model`, `provider`, `basis` | attributed writer; `basis` is `content`, `commit`, or empty |
| `harness`, `session`, `subagent` | where the writing happened |
| `n_snip`, `n_match`, `purity`, `models` | probes searched, found, share going to `model`, full tally |
| `commit_model`, `commit_session`, `key` | who committed; commit author or signing key |

`notes.csv`, one row per file: `creator_model` wrote the first revision, `main_model` the
largest share of all words ever added (`main_share`), and `words_by_model` is the full split.

The database also keeps every revision's text (`revisions.text`, `revisions.added`, zlib) and
`reads`: Claude Code `Read` calls on paths inside the repository, by session and model.

## How wrong can it be

`auctor check` samples content-attributed revisions and asks, over the whole transcript record
up to each commit, how many sessions and models ever emitted each probe. A probe emitted by
one model is unambiguous. One emitted by several, such as a template line, credits whoever
came first. The report says how many revisions rest on such probes alone.

## Limits

- `added_words` counts a sentence each time it is newly written. A file rewritten five times
  is counted five times: it measures writing done, not text standing.
- A revision whose probes trace to two models is credited whole to the plurality model.
  `purity` and `models` record the split; the words are not divided.
- Text a person typed and an agent committed is credited to no one, or to the committer if
  the revision is too short to probe. There is no evidence of human writing here, only its absence.
- Uncommitted files are not in history and not in the tables.
- Codex reads go through shell commands and are not in `reads`.
- Git merges and renames are not followed; a renamed file starts over as a new one.

## Another harness

Add a function to `src/auctor/transcripts.py` that walks the harness's transcripts and calls
`_insert` once per tool call that can write, with the session, time, model and the call's
input text, and register it in `EXTRACTORS`. Nothing else changes.

## Tests

```bash
python3 -m unittest discover -s tests
```

The suite builds a small git repository and synthetic transcripts in a temporary directory:
two models, a note committed by a session that did not write it, a transcript that survives
only in a snapshot, and an edit too short to probe.

## How this was produced

The code was written by Claude (Fable 5.2) in a Claude Code session on 2026-11-21, at the
author's direction, for a study of a notes vault of millions of authored words.

## License

GPL-3.0-or-later. See `LICENSE`.
