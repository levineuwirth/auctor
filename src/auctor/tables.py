"""Roll the evidence up into the two tables people read, and export them.

revision_attr  one row per file revision   -> revisions.csv
notes          one row per file            -> notes.csv
"""
import csv, json, os, re
from collections import Counter, defaultdict
from datetime import datetime, timezone


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _model(m):
    return re.sub(r"\[.*\]$", "", m) if m else m


def provider(cfg, model):
    if not model:
        return None
    return next((p for prefix, p in cfg.providers.items() if model.startswith(prefix)), "other")


def export(cfg, db):
    os.makedirs(cfg.out, exist_ok=True)
    for table, name in (("revision_attr", "revisions.csv"), ("notes", "notes.csv")):
        cur = db.execute(f"select * from {table}")
        with open(os.path.join(cfg.out, name), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur)


def build(cfg, db):
    db.executescript("""
    drop table if exists revision_attr;
    create table revision_attr (rev int primary key, hash text, ts text, path text, kind text, status text,
        words int, added_words int, model text, provider text, basis text, harness text, session text,
        subagent int, purity real, n_snip int, n_match int, models text, commit_model text,
        commit_session text, key text);
    drop table if exists notes;
    create table notes (path text primary key, kind text, created text, last_revised text, n_revisions int,
        final_words int, authored_words int, creator_model text, creator_provider text, creator_basis text,
        main_model text, main_share real, n_models int, words_by_model text, in_tree int);
    """)
    q = """select r.id, r.hash, r.ts, r.path, r.status, r.words, r.added_words,
                  ra.n_snip, ra.n_match, ra.harness, ra.session, ra.subagent, ra.model, ra.share, ra.models,
                  ca.harness, ca.session, ca.subagent, ca.model, c.key
           from revisions r join commits c using (hash)
           left join rev_attr ra on ra.rev = r.id left join commit_attr ca on ca.hash = r.hash
           order by r.ts, r.id"""
    per = defaultdict(list)
    for (rid, h, ts, path, st, words, aw, nsn, nm, ch, cs, csub, cm, share, models,
         kh, ks, ksub, km, key) in db.execute(q).fetchall():
        if st == "D":
            model = basis = harness = session = sub = None
        elif nm:
            model, basis, harness, session, sub = cm, "content", ch, cs, csub
        elif km:
            model, basis, harness, session, sub = km, "commit", kh, ks, ksub
        else:
            model = basis = harness = session = sub = None
        model = _model(model)
        row = (rid, h, _iso(ts), path, path.split("/")[0] if "/" in path else "(root)", st, words, aw,
               model, provider(cfg, model), basis, harness, session, sub, share, nsn, nm, models,
               _model(km), ks, key)
        db.execute("insert into revision_attr values (" + ",".join("?" * len(row)) + ")", row)
        per[path].append(row)

    for path, rows in per.items():
        live = [r for r in rows if r[5] != "D"]
        if not live:
            continue
        by = Counter()
        for r in live:
            by[r[8] or "unattributed"] += r[7]
        total = sum(by.values()) or 1
        main, mw = by.most_common(1)[0]
        first = live[0]
        db.execute("insert into notes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (path, first[4], first[2], live[-1][2], len(live), live[-1][6], sum(by.values()),
                    first[8], first[9], first[10], main, round(mw / total, 3),
                    len([m for m in by if m != "unattributed"]), json.dumps(dict(by.most_common())),
                    int(os.path.exists(os.path.join(cfg.repo, path)))))
    db.commit()
    export(cfg, db)
    total = db.execute("select sum(added_words) from revision_attr").fetchone()[0] or 0
    print(f"tables: {len(per)} files, {total} authored words")
    for basis, n, w in db.execute("select basis, count(*), sum(added_words) from revision_attr "
                                  "where status != 'D' group by 1 order by 3 desc"):
        print(f"  {basis or 'unattributed':<13} {n:>6} revisions  {100 * (w or 0) / (total or 1):5.1f}% of words")
