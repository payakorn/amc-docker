# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl"]
# ///
"""Merge per-section student rosters into one id,name,section CSV for AMC.

AMC's association/marking step works over a *single* list and matches each
scanned \\AMCcode ID to exactly one row in it. When a course runs multiple
sections through the same printed exam, the roster export is usually one
file (or one sheet) per section -- this script combines them into the one
list AMC actually needs, tagging each row with its section so results can
still be split for per-section reporting after marking. Marking itself only
needs to run once, over the combined list -- see the amc-exam skill's
"Grading setup" section.

Each input file may be .csv or .xlsx (first sheet is read). A header row
whose first cell is "id" (case-insensitive) is skipped automatically; only
the first two non-empty columns of each remaining row are used, so trailing
empty columns from an Excel export (a common ",,,,," tail) are harmless.

Usage:
    uv run tools/merge_section_lists.py OUTPUT.csv 001=list-sec001.csv 002=list-sec002.xlsx [...]

Exits with an error (and no output written) if the same student ID appears
in more than one input -- that collision would make AMC's association step
ambiguous, so it must be resolved by hand before marking.
"""
import csv
import pathlib
import sys


def read_rows(path: pathlib.Path) -> list[tuple[str, str]]:
    if path.suffix.lower() == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        raw_rows = wb[wb.sheetnames[0]].iter_rows(values_only=True)
        raw_rows = [[("" if c is None else str(c)) for c in row] for row in raw_rows]
    else:
        with path.open(newline="", encoding="utf-8-sig") as f:
            raw_rows = list(csv.reader(f))

    rows = []
    for row in raw_rows:
        cells = [c.strip() for c in row if c is not None and str(c).strip() != ""]
        if not cells:
            continue
        if cells[0].lower() == "id":
            continue
        if len(cells) < 2:
            print(f"  skipping malformed row in {path.name}: {row!r}", file=sys.stderr)
            continue
        rows.append((cells[0], cells[1]))
    return rows


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1

    out_path = pathlib.Path(argv[1])
    specs = argv[2:]

    merged: list[tuple[str, str, str]] = []
    seen: dict[str, str] = {}  # id -> "section (file)"
    dupes: list[str] = []

    for spec in specs:
        if "=" not in spec:
            print(f"error: expected SECTION=FILE, got {spec!r}", file=sys.stderr)
            return 1
        section, file_str = spec.split("=", 1)
        path = pathlib.Path(file_str)
        if not path.exists():
            print(f"error: file not found: {path}", file=sys.stderr)
            return 1

        rows = read_rows(path)
        print(f"  {path.name}: {len(rows)} students -> section {section}")
        for student_id, name in rows:
            origin = f"{section} ({path.name})"
            if student_id in seen:
                dupes.append(f"{student_id}: {seen[student_id]} vs {origin}")
            seen[student_id] = origin
            merged.append((student_id, name, section))

    if dupes:
        print("\nerror: duplicate student ID(s) across sections -- resolve before merging:", file=sys.stderr)
        for d in dupes:
            print(f"  {d}", file=sys.stderr)
        return 1

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "section"])
        writer.writerows(merged)

    print(f"\nwrote {len(merged)} students total -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
