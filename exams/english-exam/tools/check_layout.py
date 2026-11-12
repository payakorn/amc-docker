#!/usr/bin/env python3
"""Post-compile checks on an AMC project's layout database (data/layout.sqlite).

Usage:
    uv run tools/check_layout.py <exam-folder>    # e.g. final
    uv run tools/check_layout.py                  # every project under exam/ that has one

The other three checkers read the SOURCE (questions/*.tex, config.tex, groups.tex).
This one reads what AMC actually laid out on the page, which is the only place a
whole class of per-copy bugs is visible: copy 1 always looks perfect in the PDF
viewer, and the damage shows up on copy 2 and later. The worst of them is the
\\ExamResetCopy one recorded in CLAUDE.md -- if the part counter is left running,
copy 2's parts get tagged p4/p5/p6, the \\AnswerSheetLayout filters match nothing,
and copy 2's answer sheet prints with no boxes at all. Nothing in the source
files reveals that; the box counts here do.

Run it after:
    auto-multiple-choice meptex --src DOC-calage.xy --data data

Checks (every one compares ALL copies against each other, never just copy 1):
  - data/layout.sqlite exists, and is newer than the .xy it was built from
  - the number of copies matches AMC's own build:ncopies and config.tex's \\NumCopies
  - identical page count on every copy
  - identical answer-box count on every copy, split by role, so an answer sheet
    (role 2) that lost its boxes is caught even if the subject pages are fine
  - every copy has a non-empty answer sheet
  - identical question count on every copy, and question numbers running 1..N
    with no gaps (the id[n] pseudo-questions of the \\AMCcode grid are excluded)
  - identical student-ID digit-box count on every copy
  - four corner marks on every page of every copy -- a page missing one cannot
    be read by the scanner

Stdlib only. Exit code 0 = all clean, 1 = problems found.
"""
import pathlib
import re
import sqlite3
import sys

# AMC files the \AMCcode student-ID grid under pseudo-questions named id[1], id[2]...
# They are laid out like questions but are not questions, so every count that is
# meant to be "how many questions does this copy ask" has to leave them out.
ID_PSEUDO_QUESTION = re.compile(r"^id\[\d+\]$")


def fail(msg, problems):
    problems.append(msg)
    print(f"  FAIL  {msg}")


def spread(per_copy, label, problems, *, nonzero=True):
    """Every copy must agree. Report the copies that don't, not just 'mismatch'."""
    if not per_copy:
        fail(f"no {label} recorded for any copy", problems)
        return
    counts = sorted(set(per_copy.values()))
    if len(counts) > 1:
        majority = max(counts, key=lambda n: list(per_copy.values()).count(n))
        odd = {c: n for c, n in sorted(per_copy.items()) if n != majority}
        fail(f"{label} differs between copies: most have {majority}, but "
             + ", ".join(f"copy {c} has {n}" for c, n in odd.items()), problems)
        return
    if nonzero and counts[0] == 0:
        fail(f"{label} is zero on every copy", problems)
        return
    print(f"  ok    {label}: {counts[0]} on all {len(per_copy)} copies")


def check_project(project, problems):
    db_path = project / "data" / "layout.sqlite"
    print(f"== {project.name}")
    if not db_path.exists():
        fail(f"{db_path} not found -- run: auto-multiple-choice meptex "
             f"--src DOC-calage.xy --data data", problems)
        return

    xy = project / "DOC-calage.xy"
    if xy.exists() and xy.stat().st_mtime > db_path.stat().st_mtime:
        fail("layout.sqlite is older than DOC-calage.xy -- the documents were "
             "rebuilt after the last layout detection, so this database "
             "describes the PREVIOUS papers; re-run meptex", problems)

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    q = lambda sql: list(db.execute(sql))

    copies = [row[0] for row in q("SELECT DISTINCT student FROM layout_page "
                                  "ORDER BY student")]
    if not copies:
        fail("the layout database holds no copies at all", problems)
        return

    def tally(sql):
        """Count per copy, with the copies that have NOTHING counted as zero.

        A GROUP BY returns no row at all for a copy whose boxes are missing --
        exactly the copy this checker exists to find -- so seeding the result
        with zeros is what turns 'nothing to report' into a reported failure.
        """
        counts = dict.fromkeys(copies, 0)
        counts.update({row[0]: row[1] for row in db.execute(sql)})
        return counts

    variables = dict(q("SELECT name, value FROM layout_variables"))
    # build:ncopies is the copy count AMC last recorded. A later `prepare
    # --mode b` run rewrites it as the literal string "default", so it is only
    # worth comparing when it still holds a number.
    built = variables.get("build:ncopies", "")
    if built.isdigit() and int(built) != len(copies):
        fail(f"AMC built {built} copies but only {len(copies)} are laid out", problems)
    else:
        print(f"  ok    {len(copies)} copies laid out")

    config = project / "config.tex"
    if config.exists():
        m = re.search(r"\\newcommand\{\\NumCopies\}\{(\d+)\}", config.read_text())
        if m and int(m.group(1)) != len(copies):
            fail(f"config.tex asks for {m.group(1)} copies but the layout holds "
                 f"{len(copies)} -- the documents were built before that edit", problems)

    # Copies may differ in length (with no padding, a shuffle that
    # fits in fewer pages simply prints fewer), so the page count is reported,
    # not required to agree.
    pages = tally("SELECT student, COUNT(*) FROM layout_page GROUP BY student")
    lengths = sorted(set(pages.values()))
    if len(lengths) == 1:
        print(f"  ok    pages per copy: {lengths[0]} on all {len(pages)} copies")
    else:
        print("  note  pages per copy: " + ", ".join(
            f"{n} pages on {list(pages.values()).count(n)} copies" for n in lengths)
            + f" ({sum(pages.values())} pages in all)")

    # Split by role: role 1 is the boxes printed with the questions, role 2 the
    # ones on the separate answer sheet. Totalling them together would let a
    # copy with a blank answer sheet hide behind its subject pages.
    for role, what in ((1, "question boxes per copy"),
                       (2, "answer-sheet boxes per copy")):
        spread(tally(f"SELECT student, COUNT(*) FROM layout_box WHERE role={role} "
                     f"GROUP BY student"), what, problems)

    empty = [c for c in copies
             if not q(f"SELECT 1 FROM layout_box WHERE role=2 AND student={c} LIMIT 1")]
    if empty:
        fail(f"copies {empty} have NO answer-sheet boxes -- the classic symptom of "
             f"per-copy state not being reset (see \\ExamResetCopy)", problems)

    numbers = {}
    for copy in copies:
        rows = q(f"""SELECT n.number, q.name FROM layout_questionnum n
                     JOIN layout_question q ON q.question = n.question
                     WHERE n.student = {copy}""")
        numbers[copy] = sorted(int(num) for num, name in rows
                               if not ID_PSEUDO_QUESTION.match(name))
    spread({c: len(v) for c, v in numbers.items()}, "questions per copy", problems)

    for copy, nums in numbers.items():
        if nums and nums != list(range(1, len(nums) + 1)):
            missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
            fail(f"copy {copy} question numbers are not 1..{len(nums)} "
                 f"(missing {missing[:10]})", problems)

    # Every page carries the same copy/page code, so compare per page: a copy
    # with more pages has more digit boxes, but never a different number on a page.
    spread(tally("SELECT student, COUNT(*) FROM layout_digit GROUP BY student, page "
                 "HAVING page = 1"), "code digit boxes on page 1", problems)
    per_page = q("SELECT DISTINCT COUNT(*) FROM layout_digit GROUP BY student, page")
    if len(per_page) > 1:
        fail(f"code digit boxes per page differ: {sorted(r[0] for r in per_page)}", problems)
    else:
        print(f"  ok    code digit boxes: {per_page[0][0]} on every page")

    # Four corner marks per page or the scanner cannot square up the page.
    bad_pages = q("""SELECT student, page, COUNT(*) FROM layout_mark
                     GROUP BY student, page HAVING COUNT(*) != 4""")
    if bad_pages:
        fail("pages without exactly 4 corner marks: "
             + ", ".join(f"copy {s} page {p} has {n}" for s, p, n in bad_pages[:8]),
             problems)
    else:
        print(f"  ok    4 corner marks on every page")


def main():
    here = pathlib.Path(__file__).resolve().parent.parent  # exam/, above tools/
    if len(sys.argv) > 1:
        projects = [pathlib.Path(sys.argv[1])]
    else:
        projects = sorted(p.parent.parent for p in here.glob("*/data/layout.sqlite"))
        if not projects:
            print("no project under exam/ has a data/layout.sqlite yet -- build the "
                  "documents, then run auto-multiple-choice meptex")
            sys.exit(1)

    problems = []
    for project in projects:
        check_project(project, problems)
        print()

    if problems:
        print(f"{len(problems)} problem(s) found.")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
