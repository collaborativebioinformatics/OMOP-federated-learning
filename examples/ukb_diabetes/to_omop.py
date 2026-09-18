"""Map every site to OMOP with the omop-etl skill, then validate it against the contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).parent
SKILL = HERE.parents[1] / "omop_skill" / "scripts"
sys.path.insert(0, str(SKILL))

import run_mapping  # noqa: E402
import validate_contract  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=HERE / "data" / "raw")
    parser.add_argument("--out", type=Path, default=HERE / "data" / "omop")
    parser.add_argument("--mapping", type=Path, default=HERE.parents[1] / "omop_skill" / "mappings" / "ukb_pilot.yaml")
    args = parser.parse_args()

    sites = sorted(path for path in args.raw.iterdir() if path.is_dir())
    if not sites:
        raise SystemExit(f"no site folders under {args.raw}; run prepare.py first")

    failures = 0
    for site in sites:
        counts = run_mapping.run(args.mapping, site, args.out / site.name)
        problems = validate_contract.check(args.out / site.name)
        failures += len(problems)
        summary = "  ".join(f"{table} {rows:,}" for table, rows in counts.items())
        print(f"{site.name}  {summary}  {'ok' if not problems else 'FAILED'}")
        for problem in problems:
            print(f"    {problem}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
