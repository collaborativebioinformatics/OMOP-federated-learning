"""Split the UK Biobank synthetic extract into one raw folder per assessment centre."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
SAMPLE = HERE.parents[1] / "ukb_omop_agent" / "ukb" / "data" / "ukb_sampled"

# Target file name that ukb_pilot.yaml expects -> source TSV and the fields it must keep.
FILES = {
    "integer_no_arrays": ("integer_no_arrays.tsv", ("31", "34", "54")),
    "datetime_fields": ("datetime_fields.tsv", ("53",)),
    "real_fields1": ("real_fields1.tsv", ("21001",)),
    "integer_arrays_part1": ("integer_arrays_part1.tsv", ("4080",)),
    "string_fields2": ("string_fields2.tsv", ("41270",)),
    "41280_HES_SimDates": ("41280_HES_SimDates.tsv", ("41280",)),
}
CENTRE = "54-0.0"


def columns(path: Path, fields: tuple[str, ...]) -> list[str]:
    """List the instance and array columns of the wanted UKB fields.

    Args:
        path: A UKB tab-separated field-group file.
        fields: Field IDs to keep.

    Returns:
        ``eid`` followed by the matching column names, in file order.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle, delimiter="\t"))
    return ["eid"] + [name for name in header if name.split("-", 1)[0] in fields and name != "eid"]


def load(con: duckdb.DuckDBPyConnection, sample: Path) -> None:
    """Register every sampled UKB file as a DuckDB view over its wanted columns.

    Args:
        con: The connection to register into.
        sample: Directory holding the sampled TSVs.

    Raises:
        FileNotFoundError: If a required file is missing.
    """
    for name, (filename, fields) in FILES.items():
        path = sample / filename
        if not path.is_file():
            raise FileNotFoundError(f"missing UKB sample file {path}; run sample_ukb_fields.py first")
        wanted = ", ".join(f'"{column}"' for column in columns(path, fields))
        con.execute(
            f'create view "{name}" as select {wanted} from '
            f"read_csv('{path}', delim='\t', header=true, all_varchar=true, sample_size=-1)"
        )


def write_sites(con: duckdb.DuckDBPyConnection, out: Path, centres: int) -> list[dict[str, object]]:
    """Write one raw folder per assessment centre, in UKB's own wide layout.

    Args:
        con: A connection with the sample files registered.
        out: Directory to write site folders into.
        centres: How many of the largest centres to keep.

    Returns:
        One record per written site.
    """
    con.execute(
        f'create table roster as select eid, "{CENTRE}" as centre from integer_no_arrays where "{CENTRE}" <> \'\''
    )
    chosen = con.execute(
        "select centre, count(*) n from roster group by 1 order by n desc, centre limit ?", [centres]
    ).fetchall()
    written = []
    for centre, people in chosen:
        site = out / f"centre_{centre}"
        site.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            con.execute(
                f"copy (select f.* from \"{name}\" f join roster r using (eid) where r.centre = '{centre}' "
                f"order by cast(f.eid as bigint)) to '{site / f'{name}.csv'}' (header, delimiter ',')"
            )
        written.append({"centre": centre, "people": people})
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=SAMPLE, help="directory of sampled UKB TSVs")
    parser.add_argument("--out", type=Path, default=HERE / "data" / "raw", help="where to write the site folders")
    parser.add_argument("--centres", type=int, default=8, help="how many of the largest centres to keep")
    args = parser.parse_args()

    con = duckdb.connect()
    load(con, args.sample)
    args.out.mkdir(parents=True, exist_ok=True)
    sites = write_sites(con, args.out, args.centres)

    total = con.execute("select count(*) from roster").fetchone()[0]
    print(f"{total:,} participants have an assessment centre, {len(sites)} centres kept")
    for site in sites:
        print(f"  centre_{site['centre']}  {site['people']:>6,} people")
    (args.out / "manifest.json").write_text(json.dumps({"sites": sites, "participants": total}, indent=2) + "\n")


if __name__ == "__main__":
    main()
