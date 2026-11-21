import argparse, sqlite3

from . import attribute, check, config, history, tables, transcripts

STEPS = {
    "transcripts": transcripts.build,   # additive
    "history": history.build,           # rebuilds
    "attribute": attribute.build,       # rebuilds
    "tables": tables.build,             # rebuilds, exports CSV
}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="auctor", description="Attribute a repository's prose to the models that wrote it.")
    ap.add_argument("-c", "--config", default="auctor.toml")
    ap.add_argument("step", choices=[*STEPS, "all", "check"])
    ap.add_argument("--sample", type=int, default=300, help="revisions sampled by check")
    args = ap.parse_args(argv)
    cfg = config.load(args.config)
    db = sqlite3.connect(cfg.db)
    if args.step == "check":
        check.run(cfg, db, sample=args.sample)
        return
    for name in (STEPS if args.step == "all" else [args.step]):
        STEPS[name](cfg, db)


if __name__ == "__main__":
    main()
