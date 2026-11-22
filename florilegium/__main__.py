"""python -m florilegium <command>

  import  DB  FILE_OR_FOLDER...  [--user NAME] [--char NAME] [--apply]
          SillyTavern .json, or a folder of .txt entries. Dry run unless --apply. Upserts by name.
  export  DB  FOLDER  [--english] [--force]      database -> one .txt per entry
  tojson  FOLDER  OUT.json                       folder -> SillyTavern-shaped json
  check   FOLDER                                 duplicate names, shared keys, empty bodies, never-firing entries
  preview DB  "latest user line" ["earlier line" ...]
          what would fire next turn and the exact block, without touching any state
  list    DB
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import folder as F
from .engine import Lorebook
from .importers import read_json, to_sillytavern


def _gather(paths: list[str], user: str | None, char: str | None):
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            yield p, F.load(p)
        elif p.suffix.lower() == ".json":
            yield p, read_json(p, user=user, char=char)
        elif p.suffix.lower() == ".txt":
            yield p, [F.parse(p)]
        else:
            print(f"skip {p}: not a folder, .json or .txt")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m florilegium", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("import"); a.add_argument("db"); a.add_argument("files", nargs="+")
    a.add_argument("--user"); a.add_argument("--char"); a.add_argument("--apply", action="store_true")
    a.add_argument("--keep-keyless", action="store_true", help="also import entries with no keys that are not constant")
    x = sub.add_parser("export"); x.add_argument("db"); x.add_argument("folder")
    x.add_argument("--english", action="store_true"); x.add_argument("--force", action="store_true")
    t = sub.add_parser("tojson"); t.add_argument("folder"); t.add_argument("out")
    c = sub.add_parser("check"); c.add_argument("folder")
    p = sub.add_parser("preview"); p.add_argument("db"); p.add_argument("lines", nargs="+")
    li = sub.add_parser("list"); li.add_argument("db")
    a_ = ap.parse_args(argv)

    if a_.cmd == "import":
        book = Lorebook(a_.db) if a_.apply else None
        n_ok = n_skip = 0
        for src, ents in _gather(a_.files, a_.user, a_.char):
            print(f"{src}: {len(ents)} entries")
            for e in ents:
                if not e.keys and not e.constant and not a_.keep_keyless:
                    n_skip += 1
                    print(f"  skip {e.name!r}: no keys and not constant")
                    continue
                n_ok += 1
                if book:
                    book.upsert(e)
        print(("wrote" if book else "would write (dry run, add --apply)") + f" {n_ok}, skipped {n_skip}")
        return 0
    if a_.cmd == "export":
        n = F.export(Lorebook(a_.db).entries(), Path(a_.folder), english=a_.english, force=a_.force)
        print(f"wrote {n} files -> {a_.folder}")
        return 0
    if a_.cmd in ("tojson", "check"):
        probs = F.check(Path(a_.folder))
        if probs:
            print("problems:\n  " + "\n  ".join(probs))
            return 1
        if a_.cmd == "check":
            print(f"ok: {len(F.walk(Path(a_.folder)))} entries")
            return 0
        Path(a_.out).write_text(json.dumps(to_sillytavern(F.load(Path(a_.folder))), ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"wrote {a_.out}")
        return 0
    if a_.cmd == "preview":
        msgs = [("user", t) for t in reversed(a_.lines)]
        block, kept, dropped = Lorebook(a_.db).preview(msgs)
        for k in kept:
            print(f"  + {k.entry.name}  ({k.cause}{', sticky' if k.via == 'sticky' else ''})")
        for d in dropped:
            print(f"  - {d}  (over budget)")
        print("\n" + (block or "(nothing fires)"))
        return 0
    if a_.cmd == "list":
        for e in Lorebook(a_.db).entries():
            flag = "constant" if e.constant else ", ".join(e.keys)
            print(f"{'  ' if e.enabled else 'x '}{e.name}  [{flag}]  order {e.order}, {len(e.content)} chars")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
