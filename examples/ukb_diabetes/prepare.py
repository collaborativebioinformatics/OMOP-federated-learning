"""Turn the official UK Biobank synthetic tabular extract into one raw CSV folder per assessment centre.

This is source preparation, not OMOP mapping: it reshapes UKB's wide instance and array
columns into tidy per-event rows and keeps the source codes untouched.
``to_omop.py`` does the concept mapping afterwards.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
SAMPLE = HERE.parents[1] / "ukb_omop_agent" / "ukb" / "data" / "ukb_sampled"

FILES = {
    "integers": "integer_no_arrays.tsv",
    "reals": "real_fields1.tsv",
    "arrays": "integer_arrays_part1.tsv",
    "dates": "datetime_fields.tsv",
    "icd": "string_fields2.tsv",
    "icd_dates": "41280_HES_SimDates.tsv",
}
SEX, BIRTH_YEAR, CENTRE, VISIT_DATE = "31-0.0", "34-0.0", "54-0.0", "53-0.0"
BMI_FIELD, SBP_FIELD, ICD_FIELD, ICD_DATE_FIELD = "21001", "4080", "41270", "41280"


def columns(path: Path, field: str) -> list[str]:
    """List the instance and array columns of one UKB field.

    Args:
        path: A UKB tab-separated field-group file.
        field: The field ID.

    Returns:
        The matching column names, in file order.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle, delimiter="\t"))
    return [name for name in header if name.startswith(f"{field}-")]


def _visit_of(column: str) -> int:
    return int(column.split("-", 1)[1].split(".", 1)[0])


def _slot_of(column: str) -> int:
    return int(column.split(".", 1)[1])


def _visit_date(column: str) -> str:
    return 'd."' + VISIT_DATE.replace("-0.", f"-{_visit_of(column)}.") + '"'


def load(con: duckdb.DuckDBPyConnection, sample: Path) -> None:
    """Register every sampled UKB file as a DuckDB view.

    Args:
        con: The connection to register into.
        sample: Directory holding the sampled TSVs.

    Raises:
        FileNotFoundError: If a required file is missing.
    """
    for name, filename in FILES.items():
        path = sample / filename
        if not path.is_file():
            raise FileNotFoundError(f"missing UKB sample file {path}; run sample_ukb_fields.py first")
        con.execute(
            f"create view {name} as select * from "
            f"read_csv('{path}', delim='\t', header=true, all_varchar=true, sample_size=-1)"
        )


def unpivot(values: list[str], dates: list[str], source: str) -> str:
    """Pair value columns with their date columns by position and return one row per pair.

    Args:
        values: Value column names in the source.
        dates: SQL expressions for the matching date columns, in the same order.
        source: The FROM clause the columns come from.

    Returns:
        A query yielding ``eid``, ``value`` and ``event_date``.
    """
    value_list = ", ".join(f'"{name}"' for name in values)
    date_list = ", ".join(dates)
    # Two unnests in one select expand positionally, pairing each value with the date at the same index.
    return f"select eid, unnest([{value_list}]) as value, unnest([{date_list}]) as event_date from {source}"


def build(con: duckdb.DuckDBPyConnection, sample: Path) -> None:
    """Reshape the wide UKB columns into participant, measurement and diagnosis tables.

    Args:
        con: A connection with the sample files registered.
        sample: Directory holding the sampled TSVs.
    """
    con.execute(f"""
        create table participants as
        select i.eid,
               i."{SEX}" as sex,
               i."{BIRTH_YEAR}" as year_of_birth,
               i."{CENTRE}" as centre,
               d."{VISIT_DATE}" as visit_date
        from integers i join dates d using (eid)
        where i."{CENTRE}" <> '' and d."{VISIT_DATE}" <> ''
    """)

    # Each reading is dated by the assessment visit it was taken at, never by another visit.
    bmi = columns(sample / FILES["reals"], BMI_FIELD)
    sbp = columns(sample / FILES["arrays"], SBP_FIELD)
    bmi_query = unpivot(bmi, [_visit_date(name) for name in bmi], "reals r join dates d using (eid)")
    sbp_query = unpivot(sbp, [_visit_date(name) for name in sbp], "arrays a join dates d using (eid)")
    con.execute(f"""
        create table measurements as
        select eid, field, value, event_date as measured_on from (
            select '{BMI_FIELD}' as field, * from ({bmi_query})
            union all
            select '{SBP_FIELD}' as field, * from ({sbp_query})
        )
        where value <> '' and event_date <> ''
    """)

    # Array slot 0 carries no paired date column in the UKB extract, so it cannot become a dated event.
    codes = columns(sample / FILES["icd"], ICD_FIELD)
    dated = {_slot_of(name) for name in columns(sample / FILES["icd_dates"], ICD_DATE_FIELD)}
    paired = [name for name in codes if _slot_of(name) in dated]
    date_columns = ['t."' + name.replace(ICD_FIELD, ICD_DATE_FIELD) + '"' for name in paired]
    diagnosis_query = unpivot(paired, date_columns, "icd s join icd_dates t using (eid)")
    con.execute(f"""
        create table diagnoses as
        select eid, value as icd10, event_date as diagnosed_on from ({diagnosis_query})
        where value <> '' and event_date <> ''
    """)

    unpaired = ", ".join(f'"{name}"' for name in codes if _slot_of(name) not in dated)
    con.execute(
        f"create table undated as select count(*) n from icd s, unnest([{unpaired}]) as u(code) where u.code <> ''"
    )


def write_sites(con: duckdb.DuckDBPyConnection, out: Path, centres: int) -> list[dict[str, object]]:
    """Write one raw CSV folder per assessment centre.

    Args:
        con: A connection holding the reshaped tables.
        out: Directory to write site folders into.
        centres: How many of the largest centres to keep.

    Returns:
        One record per written site.
    """
    chosen = con.execute(
        "select centre, count(*) n from participants group by 1 order by n desc, centre limit ?", [centres]
    ).fetchall()
    written = []
    for centre, _ in chosen:
        site = out / f"centre_{centre}"
        site.mkdir(parents=True, exist_ok=True)
        counts: dict[str, object] = {"centre": centre}
        for table in ("participants", "measurements", "diagnoses"):
            scope = "" if table == "participants" else "join participants using (eid)"
            con.execute(
                f"copy (select {table}.* from {table} {scope} where centre = '{centre}' order by eid) "
                f"to '{site / f'{table}.csv'}' (header, delimiter ',')"
            )
            counts[table] = con.execute(f"select count(*) from {table} {scope} where centre = '{centre}'").fetchone()[0]
        written.append(counts)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=SAMPLE, help="directory of sampled UKB TSVs")
    parser.add_argument("--out", type=Path, default=HERE / "data" / "raw", help="where to write the site folders")
    parser.add_argument("--centres", type=int, default=8, help="how many of the largest centres to keep")
    args = parser.parse_args()

    con = duckdb.connect()
    load(con, args.sample)
    build(con, args.sample)
    args.out.mkdir(parents=True, exist_ok=True)
    sites = write_sites(con, args.out, args.centres)

    people = con.execute("select count(*) from participants").fetchone()[0]
    undated = con.execute("select n from undated").fetchone()[0]
    print(f"{people:,} participants with an assessment centre and a first assessment date")
    print(f"{undated:,} diagnosis codes dropped: UKB array slot 0 has no paired date column")
    for site in sites:
        print(
            f"  centre_{site['centre']}  {site['participants']:>6,} people  "
            f"{site['measurements']:>7,} measurements  {site['diagnoses']:>8,} diagnoses"
        )
    (args.out / "manifest.json").write_text(json.dumps({"sites": sites, "undated_codes": undated}, indent=2) + "\n")


if __name__ == "__main__":
    main()
