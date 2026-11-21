"""Join history to transcripts. Two independent lines of evidence per revision:

commit   the tool call that carried this commit's message, just before the commit time:
         names the session and model that committed.
content  sentences first appearing in this revision, found in what a session emitted
         before the commit: names the session and model that wrote.

Content is preferred where it exists; one session can commit another's prose.
"""
import bisect, sys, zlib
from collections import Counter

from .transcripts import norm

SNIP, MINLEN, NSNIP = 40, 60, 8   # probe length, shortest sentence probed, probes per revision
DAY = 86400


def probes(added_text):
    """Up to NSNIP probes spread through a revision's new sentences: the middle SNIP
    normalized characters of each sentence long enough to be distinctive."""
    sents = [s for s in (norm(x) for x in added_text.split("\n")) if len(s) >= MINLEN]
    if not sents:
        return []
    step = max(1, len(sents) // NSNIP)
    return [s[len(s) // 2 - SNIP // 2: len(s) // 2 + SNIP // 2] for s in sents[::step][:NSNIP]]


class Index:
    """Every tool call's normalized text, concatenated in time order, so that a search
    over a time window is one substring find between two offsets."""

    def __init__(self, db):
        self.ev = db.execute("select id, harness, session, subagent, ts, model, norm from events order by ts").fetchall()
        self.ts = [e[4] for e in self.ev]
        self.norm = [zlib.decompress(e[6]).decode() for e in self.ev]
        self.offs, pos = [], 0
        for n in self.norm:
            self.offs.append(pos)
            pos += len(n) + 1
        self.big = "|".join(self.norm) + "|"
        self.by_session = {}
        for i, e in enumerate(self.ev):
            self.by_session.setdefault(e[2], []).append(i)

    def _span(self, lo_ts, hi_ts):
        a, b = bisect.bisect_right(self.ts, lo_ts), bisect.bisect_right(self.ts, hi_ts)
        return a, b, (self.offs[b] if b < len(self.offs) else len(self.big))

    def first(self, snip, lo_ts, hi_ts):
        """Earliest event in (lo_ts, hi_ts] whose text contains snip."""
        a, b, end = self._span(lo_ts, hi_ts)
        if a >= b:
            return None
        p = self.big.find(snip, self.offs[a], end)
        return None if p < 0 else bisect.bisect_right(self.offs, p) - 1

    def every(self, snip, lo_ts, hi_ts):
        """All events in (lo_ts, hi_ts] whose text contains snip."""
        a, b, end = self._span(lo_ts, hi_ts)
        out = []
        while a < b:
            p = self.big.find(snip, self.offs[a], end)
            if p < 0:
                break
            i = bisect.bisect_right(self.offs, p) - 1
            out.append(i)
            a = i + 1
        return out


def build(cfg, db):
    ix = Index(db)
    ev = ix.ev
    print(f"attribute: {len(ev)} tool calls indexed", file=sys.stderr)

    db.executescript("""
    drop table if exists commit_attr;
    create table commit_attr (hash text primary key, event int, harness text, session text,
        subagent int, model text, lag real);
    drop table if exists rev_attr;
    create table rev_attr (rev int primary key, n_snip int, n_match int, harness text, session text,
        subagent int, model text, share real, models text);
    """)

    # commit line: the latest call in the half hour before the commit that carries its message
    commit_ev = {}
    for h, ts, msg in db.execute("select hash, ts, message from commits"):
        m = norm(msg)[:SNIP]
        if len(m) < 12:
            continue
        a, b = bisect.bisect_left(ix.ts, ts - 1800), bisect.bisect_right(ix.ts, ts + 15)
        hit = next((i for i in range(b - 1, a - 1, -1) if m in ix.norm[i]), None)
        if hit is None:
            continue
        e = ev[hit]
        commit_ev[h] = hit
        db.execute("insert into commit_attr values (?,?,?,?,?,?,?)", (h, e[0], e[1], e[2], e[3], e[5], ts - e[4]))

    # content line
    prev = {}
    rows = db.execute("select id, hash, path, ts, added from revisions where status != 'D' order by ts, id").fetchall()
    for rid, h, path, ts, added in rows:
        t0 = prev.get(path)
        prev[path] = ts
        snips = probes(zlib.decompress(added).decode())
        if not snips:
            db.execute("insert into rev_attr values (?,0,0,null,null,null,null,null,null)", (rid,))
            continue
        ce = commit_ev.get(h)
        own = ([i for i in ix.by_session.get(ev[ce][2], []) if ts - 14 * DAY < ix.ts[i] <= ts + 15]
               if ce is not None else [])
        hits = []
        for sn in snips:
            # the committing session first, then anyone since the file's previous revision
            hit = next((i for i in own if sn in ix.norm[i]), None)
            if hit is None:
                hit = ix.first(sn, t0 if t0 else ts - 7 * DAY, ts + 15)
            if hit is None and t0:
                hit = ix.first(sn, ts - 2 * DAY, ts + 15)
            if hit is not None:
                hits.append(hit)
        if not hits:
            db.execute("insert into rev_attr values (?,?,0,null,null,null,null,null,null)", (rid, len(snips)))
            continue
        models = Counter(ev[i][5] for i in hits)
        top, n = models.most_common(1)[0]
        mine = [i for i in hits if ev[i][5] == top]
        sess = Counter(ev[i][2] for i in mine).most_common(1)[0][0]
        e = next(ev[i] for i in mine if ev[i][2] == sess)
        sub = max(ev[i][3] for i in mine if ev[i][2] == sess)
        db.execute("insert into rev_attr values (?,?,?,?,?,?,?,?,?)",
                   (rid, len(snips), len(hits), e[1], sess, sub, top, n / len(hits),
                    ",".join(f"{m}:{c}" for m, c in models.most_common())))
    db.commit()
    n, m = db.execute("select count(*), sum(n_match > 0) from rev_attr").fetchone()
    c = db.execute("select count(*) from commit_attr").fetchone()[0]
    print(f"attribute: {m or 0} of {n} revisions matched by content, {c} commits matched to a session")
