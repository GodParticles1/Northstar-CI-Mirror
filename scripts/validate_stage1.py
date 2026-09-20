#!/usr/bin/env python3
"""Validate the Personal Tech OS Stage 1 file model."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from personal_tech_os.stage1 import Stage1ValidationError, load_bundle, validate_bundle  # noqa: E402


def main() -> int:
    try:
        result = validate_bundle(load_bundle(ROOT / "data" / "stage1"))
    except Stage1ValidationError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {"status": "PASS", "checks": result.checks, "counts": result.counts},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

