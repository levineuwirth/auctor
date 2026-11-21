"""How much could the content join be wrong?

A probe names its writer only if one model ever emitted it. A formula that several models
write ("no records were written", a template line) matches whoever emitted it first, which
may not be the writer. For a sample of content-attributed revisions, this counts, over the
whole transcript record up to the commit, how many sessions and models emitted each probe.
"""
import random, zlib

from .attribute import Index, probes


def run(cfg, db, sample=300, seed=0):
    ix = Index(db)
    rows = db.execute("""select r.id, r.ts, r.added, a.model from revisions r
                         join rev_attr a on a.rev = r.id where a.n_match > 0""").fetchall()
    random.Random(seed).shuffle(rows)
    rows = rows[:sample]
    n_probe = one_session = one_model = many_models = 0
    clean = shaky = wrong = 0
    for rid, ts, added, model in rows:
        ambiguous = False
        votes = []
        for sn in probes(zlib.decompress(added).decode()):
            hits = ix.every(sn, float("-inf"), ts + 15)
            if not hits:
                continue
            n_probe += 1
            sessions = {ix.ev[i][2] for i in hits}
            models = {ix.ev[i][5] for i in hits}
            if len(models) > 1:
                many_models += 1
                ambiguous = True
            elif len(sessions) > 1:
                one_model += 1
            else:
                one_session += 1
            if len(models) == 1:
                votes.append(next(iter(models)))
        if not ambiguous:
            clean += 1
        elif votes and max(set(votes), key=votes.count) == model:
            shaky += 1      # some probes are shared, but the unshared ones agree with the label
        else:
            wrong += 1      # nothing unshared supports the label
    pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"
    print(f"check: {len(rows)} revisions sampled, {n_probe} probes found")
    print(f"  probes emitted by one session only        {one_session:>6}  {pct(one_session, n_probe)}")
    print(f"  by several sessions of one model          {one_model:>6}  {pct(one_model, n_probe)}")
    print(f"  by more than one model                    {many_models:>6}  {pct(many_models, n_probe)}")
    print(f"  revisions with no shared probe            {clean:>6}  {pct(clean, len(rows))}")
    print(f"  shared probes, label still supported      {shaky:>6}  {pct(shaky, len(rows))}")
    print(f"  label rests only on shared probes         {wrong:>6}  {pct(wrong, len(rows))}")
    return {"revisions": len(rows), "probes": n_probe, "one_session": one_session, "one_model": one_model,
            "many_models": many_models, "clean": clean, "supported": shaky, "unsupported": wrong}
