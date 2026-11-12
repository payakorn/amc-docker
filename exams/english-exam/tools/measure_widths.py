#!/usr/bin/env python3
"""Measure the typeset width of every table in the question bank.

Usage:
    uv run tools/measure_widths.py [exam-folder]     # default: midterm-amc

Why this exists
---------------
Questions are typeset inside `\\begin{multicols}{2}`, where the usable
`\\linewidth` is only ~202pt (~7.1cm) -- much narrower than the page. A table
built from `c`/`l` columns never wraps, so it silently grows past that budget
whenever its *content* changes (e.g. renaming a header during a re-theming
pass) and spills into the neighbouring column.

That spill is shuffle-order-dependent and easy to miss: AMC reshuffles
question order per copy, so an over-wide table only visibly collides with text
when it lands in the LEFT column. In the right column it overflows into the
page's outer margin, which has enough slack to look fine. One test copy can
therefore look clean while other students' papers are corrupted.

This script extracts every `tabular` from questions/*.tex, typesets each into a
`\\sbox` with the same preamble the real exam uses, and reports its width
against the budget -- so the check does not depend on which shuffle you
happened to render.

Exit code 0 = every table fits, 1 = at least one is over budget.
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# Usable \linewidth inside \begin{multicols}{2} in this project's groups.tex.
# Re-derive with \typeout{\the\linewidth} inside a question if the layout
# (margins, columnsep, column count) ever changes.
BUDGET_PT = 207.70522
# A part can be typeset in ONE column instead (\ExamPart[1] in config.tex --
# the midterm's Part 2 and Part 3 both are), and there the usable width is the
# whole text block: both columns plus the gutter between them. A table that
# only ever prints in such a part is measured against this instead. It is opt
# in, by a "% single-column:" comment inside the question, because nothing in
# questions/ records which part a group ends up in -- that is config.tex's
# decision, and a group can move between sittings. Say in the comment which
# part it relies on, so the claim can be rechecked when the exam is reshaped.
SINGLE_COL_BUDGET_PT = 2 * BUDGET_PT + 11.38594   # + \columnsep of 0.4cm
SINGLE_COL = re.compile(r"%\s*single-column\b")
# Flag anything with less than this much slack, so a later small content edit
# doesn't silently push a "just fits" table over the line.
MIN_SLACK_PT = 4.0

PREAMBLE = r"""\documentclass[a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage{tgtermes}
\usepackage{tikz}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepgfplotslibrary{statistics}
\usetikzlibrary{positioning}
\newsavebox{\mybox}
\newcommand{\measure}[2]{%
  \sbox{\mybox}{#2}%
  \typeout{MEASURE: #1 = \the\wd\mybox}%
}
\begin{document}
"""


# Declarations that change a table's typeset width and are legal right before
# it inside the same group -- a font size, or a narrower inter-column pad.
# Anything else in the run-up (\begin{center}, whitespace) does not.
WIDTH_SETTING_RE = re.compile(
    r"\\(?:tiny|scriptsize|footnotesize|small|normalsize)\b"
    r"|\\setlength\s*\{\\tabcolsep\}\s*\{[^}]*\}"
)


def group_start(body, pos):
    """Index just after the `{` opening the group that encloses body[pos].

    Scans backwards counting braces, so balanced groups passed on the way (the
    two arguments of \setlength{\tabcolsep}{2pt}, say) are skipped and only an
    *unmatched* `{` counts as the enclosing group's opener. Returns 0 when the
    table is not inside a group at all.
    """
    depth = 0
    for i in range(pos - 1, -1, -1):
        c = body[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                return i + 1
            depth -= 1
    return 0


def extract_tables(qdir):
    """Yield (question_id, latex_snippet) for each tabular in the bank.

    The snippet is prefixed with whatever width-affecting declarations are in
    scope where the table sits -- a font size like {\small ...} or {\scriptsize
    ...}, and any \setlength{\tabcolsep}{...}. Measuring without them reports a
    table as over budget when the real exam typesets it comfortably inside the
    column, so the prefix is what makes the number match the printed page.
    """
    out = []
    files = sorted(qdir.glob("*.tex")) + sorted((qdir / "imported").glob("*.tex"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        for _env, qid, body in re.findall(
            r"\\begin\{(question|questionmultx)\}\{([^}]*)\}(.*?)\\end\{\1\}",
            text,
            re.S,
        ):
            budget = (SINGLE_COL_BUDGET_PT if SINGLE_COL.search(body)
                      else BUDGET_PT)
            for m in re.finditer(r"\\begin\{tabular\}.*?\\end\{tabular\}", body, re.S):
                table = m.group(0)
                # Look back over the run-up to the table for declarations still
                # in force at \begin{tabular}. Stop at the enclosing group's
                # opening brace so a size used earlier in the question -- and
                # already closed -- is not picked up.
                lead = body[group_start(body, m.start()) : m.start()]
                settings = "".join(WIDTH_SETTING_RE.findall(lead))
                out.append((qid, f"{{{settings}{table}}}" if settings else table,
                            budget))
    return out


def main():
    exam_folder = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "midterm-amc")
    qdir = exam_folder.parent / "questions"
    if not qdir.exists():
        qdir = pathlib.Path("questions")
    if not qdir.exists():
        sys.exit(f"no questions/ directory found next to {exam_folder}")

    if not shutil.which("pdflatex"):
        sys.exit("pdflatex not found -- this check needs a TeX installation")

    tables = extract_tables(qdir)
    if not tables:
        print("no tables found in the question bank")
        return

    budgets = {qid: budget for qid, _snippet, budget in tables}
    doc = [PREAMBLE]
    for qid, snippet, _budget in tables:
        doc.append(f"\\measure{{{qid}}}{{{snippet}}}\n")
    doc.append(r"\end{document}")

    with tempfile.TemporaryDirectory() as td:
        tex = pathlib.Path(td) / "measure.tex"
        tex.write_text("\n".join(doc), encoding="utf-8")
        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "measure.tex"],
            cwd=td,
            capture_output=True,
            text=True,
        )
        log = proc.stdout

    over, tight = [], []
    print(f"  budget: {BUDGET_PT:.2f}pt (usable \\linewidth in 2-column question area)")
    print(f"          {SINGLE_COL_BUDGET_PT:.2f}pt for questions marked "
          f"\"% single-column\"\n")
    for qid, width_s in re.findall(r"MEASURE: (\S+) = ([\d.]+)pt", log):
        width = float(width_s)
        budget = budgets.get(qid, BUDGET_PT)
        slack = budget - width
        if slack < 0:
            status = f"OVER by {-slack:.2f}pt"
            over.append(qid)
        elif slack < MIN_SLACK_PT:
            status = f"tight ({slack:.2f}pt slack)"
            tight.append(qid)
        else:
            status = f"ok ({slack:.2f}pt slack)"
        print(f"  {qid:12s} {width:8.2f}pt  {status}")

    print()
    if over:
        print(f"{len(over)} table(s) over budget: {', '.join(over)}")
        print("Shorten the header text, wrap in {\\small ...}, or use p{} columns.")
        sys.exit(1)
    if tight:
        print(f"note: {len(tight)} table(s) with little slack: {', '.join(tight)}")
    print("All tables fit.")


if __name__ == "__main__":
    main()
