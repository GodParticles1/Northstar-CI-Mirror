"""Generate or verify a deterministic Stage 1 snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from personal_tech_os.stage1 import dumps_canonical, load_bundle, validate_bundle


def default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_snapshot(root: Path) -> str:
    bundle = load_bundle(root / "data" / "stage1")
    validate_bundle(bundle)
    return dumps_canonical(bundle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path, help="write the canonical snapshot")
    group.add_argument("--check", type=Path, help="verify an existing snapshot")
    parser.add_argument("--root", type=Path, default=default_root())
    args = parser.parse_args(argv)

    rendered = build_snapshot(args.root.resolve())
    if args.output:
        output = args.output if args.output.is_absolute() else args.root / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(f"PASS wrote deterministic snapshot: {output}")
        return 0

    target = args.check if args.check.is_absolute() else args.root / args.check
    if not target.exists():
        print(f"FAIL snapshot does not exist: {target}")
        return 1
    if target.read_text(encoding="utf-8") != rendered:
        print(f"FAIL snapshot is stale: {target}")
        return 1
    print(f"PASS snapshot matches Stage 1 data: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

