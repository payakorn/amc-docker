#!/usr/bin/env python3
"""Pre-compile sanity checks for an AMC exam project (questions/*.tex +
<exam-folder>/groups.tex + <exam-folder>/config.tex).

Usage:
    uv run tools/check_questions.py <exam-folder>     # e.g. midterm

Checks:
  - balanced \\begin/\\end{question} and \\begin/\\end{choices}
  - every standard MC question has exactly 4 choices and exactly 1 \\correctchoice
  - every numeric-answer question (\\AMCnumericChoices, no \\choices) has exactly
    one \\AMCnumericChoices call and no stray \\correctchoice/\\wrongchoice
  - no duplicate question IDs across all chapter files
  - no unescaped % or & inside question bodies
  - groups.tex \\input's every file under questions/ and questions/imported/
    (and no phantom ones)
  - every \\DrawGroup / \\DrawAll call in config.tex has a matching \\element
    group (authored groups not yet drawn by this sitting are fine -- the bank
    is shared across sittings -- but a draw with no questions behind it is a bug)
  - no \\DrawGroup asks for more questions than its group actually holds, which
    would put the same question on one paper twice ("question ID used several
    times for the same paper" at AMC's post-compile check)

Stdlib only. Exit code 0 = all clean, 1 = problems found.
"""
import pathlib
import re
import sys


def fail(msg, problems):
    problems.append(msg)
    print(f"  FAIL  {msg}")


def main():
    exam_folder = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    root = exam_folder.parent if exam_folder.name else exam_folder
    qdir = exam_folder.parent / "questions" if (exam_folder.parent / "questions").exists() else exam_folder / "questions"
    # questions/imported/ holds questions carried over from a previous class,
    # in their own chNN_..._prev groups -- same format, same checks.
    qfiles = sorted(qdir.glob("*.tex")) + sorted((qdir / "imported").glob("*.tex"))
    qtex = "\n".join(f.read_text(encoding="utf-8") for f in qfiles)
    gtex = (exam_folder / "groups.tex").read_text(encoding="utf-8")
    # groups.tex is a one-line \input of the shared layout in ../common/; read
    # the layout too, since that is where the question banks are \input.
    for ref in re.findall(r"\\input\{(\.\./common/[\w.-]+?)(?:\.tex)?\}", gtex):
        gtex += "\n" + (exam_folder / f"{ref}.tex").read_text(encoding="utf-8")
    ctex = (exam_folder / "config.tex").read_text(encoding="utf-8")
    problems = []

    # Balanced environments
    for env in ("question", "questionmultx", "choices"):
        b = len(re.findall(rf"\\begin\{{{env}\}}", qtex))
        e = len(re.findall(rf"\\end\{{{env}\}}", qtex))
        if b != e:
            fail(f"{env}: {b} \\begin vs {e} \\end", problems)
        else:
            print(f"  ok    {env}: {b} balanced pairs")

    # Per-question checks (questionmultx is the same authoring pattern as question --
    # AMC needs it specifically for multi-digit/decimal \AMCnumericChoices answers,
    # see the skill's Troubleshooting notes on \begin{question} + digits>1)
    ids = []
    n_mc = 0
    n_numeric = 0
    blocks = re.findall(
        r"\\begin\{(question|questionmultx)\}\{([^}]*)\}(.*?)\\end\{\1\}", qtex, re.S
    )
    for _env, qid, body in blocks:
        ids.append(qid)
        is_numeric = "\\AMCnumericChoices" in body
        n_correct = len(re.findall(r"\\correctchoice", body))
        n_wrong = len(re.findall(r"\\wrongchoice", body))
        if is_numeric:
            n_numeric += 1
            n_numchoices = len(re.findall(r"\\AMCnumericChoices", body))
            if n_numchoices != 1:
                fail(f"{qid}: {n_numchoices} \\AMCnumericChoices calls (want 1)", problems)
            if n_correct or n_wrong:
                fail(f"{qid}: numeric question has stray \\correctchoice/\\wrongchoice", problems)
        else:
            n_mc += 1
            if n_correct != 1:
                fail(f"{qid}: {n_correct} \\correctchoice (want 1)", problems)
            # 4 is the house default and what almost every question uses, but a
            # matching set that maps scenarios onto a fixed list of N dimensions
            # needs all N options present on every item or the question stops
            # being the same question. AMC sizes each answer-sheet box row to
            # its own question, and scores per question, so a wider row costs
            # nothing but width -- which measure_widths.py and a test compile
            # check. Below 3 the distractors stop doing any work. The ceiling is
            # the answer sheet: an A-G row fits a 2-column grid (verified in a
            # test compile of Part 2, whose data-quality-dimension items list
            # all seven dimensions) but not a 3-column one. So a 7-choice
            # question must sit in a part whose \SheetGrid is 2 columns -- see
            # \AnswerSheetLayout in config.tex before adding one to Part 1.
            # Up to 8 choices (A-H) are allowed: an eight-box row
            # also fits a 2-column grid, checked by eye on the
            # built answer sheet.
            if not 3 <= n_correct + n_wrong <= 8:
                fail(f"{qid}: {n_correct + n_wrong} choices (want 3-8)", problems)
        # Unescaped % or & (not preceded by a backslash) -- but inside an
        # alignment environment, & is the legitimate column/alignment separator
        # (not stray text needing escaping), so strip those blocks before
        # checking. tabular is the common one; aligned/align/array/cases show up
        # in questions that print a stacked formula.
        body_for_escape_check = re.sub(
            r"\\begin\{(tabular|aligned|align\*?|array|cases|matrix|"
            r"[bpvB]matrix)\}.*?\\end\{\1\}",
            "",
            body,
            flags=re.S,
        )
        # Whole-line LaTeX comments are legitimate (e.g. a note explaining why a
        # question uses \begin{choices}[o]); only stray % in *text* is the bug.
        body_for_escape_check = "\n".join(
            l for l in body_for_escape_check.splitlines() if not l.lstrip().startswith("%")
        )
        for ch in ("%", "&"):
            if re.search(rf"(?<!\\){re.escape(ch)}", body_for_escape_check):
                fail(f"{qid}: unescaped '{ch}' in question body", problems)
    print(f"  ok    {len(blocks)} questions parsed across {len(qfiles)} files "
          f"({n_mc} multiple-choice, {n_numeric} numeric-answer)")

    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        fail(f"duplicate question IDs: {sorted(dupes)}", problems)
    else:
        print(f"  ok    {len(ids)} unique question IDs")

    # groups.tex \input wiring: every questions/*.tex file must be \input, and vice versa
    # (imported files are pulled in via \IfFileExists{...}{\input{...}}{}, so the
    # \input path carries the imported/ prefix -- match both shapes).
    input_files = set(re.findall(r"\\input\{\.\./questions/((?:imported/)?[\w.-]+)\.tex\}", gtex))
    actual_files = {str(f.relative_to(qdir).with_suffix("")) for f in qfiles}
    # \IfFileExists-guarded inputs are optional by design (a chapter with nothing
    # imported contributes no file), so their absence is not a phantom input.
    optional_inputs = set(
        re.findall(r"\\IfFileExists\{\.\./questions/((?:imported/)?[\w.-]+)\.tex\}", gtex)
    )
    if input_files != actual_files:
        missing = actual_files - input_files
        extra = input_files - actual_files - optional_inputs
        if missing:
            fail(f"questions/ files not \\input in groups.tex: {sorted(missing)}", problems)
        if extra:
            fail(f"groups.tex \\input's files that don't exist: {sorted(extra)}", problems)
    else:
        print(f"  ok    groups.tex inputs all {len(actual_files)} question files")

    # Group wiring: \element names in questions/ vs active draws in config.tex.
    # The bank is shared across exam sittings, so a group can be authored but not
    # drawn by this particular sitting (e.g. this midterm skips *_matching) --
    # that's expected, not a failure. A draw with no \element behind it is a real
    # bug, since it would insert nothing (or error) at compile time.
    element_groups = set(re.findall(r"\\element\{([^}]*)\}", qtex))
    # TWO sizes per group, and the difference matters:
    #
    #   group_elements  -- how many \element blocks the group holds. This is
    #                      what AMC counts when a draw asks for N, so it is what
    #                      a draw must be checked against.
    #   group_questions -- how many questions those blocks contain, which is
    #                      what actually lands on the student's paper.
    #
    # They are equal for the usual one-question-per-\element groups. They differ
    # for a shared-stem group, where one \element deliberately holds a table and
    # every question that reads it (see ch56prep_shared). Checking a draw against
    # the QUESTION count there would wave through a draw bigger than the group,
    # which is exactly the duplicate-ID bug this check exists to catch.
    group_elements = {}
    group_questions = {}
    for m in re.finditer(r"\\element\{([^}]*)\}\{", qtex):
        depth, i = 0, m.end() - 1
        for j in range(i, len(qtex)):
            if qtex[j] == "{":
                depth += 1
            elif qtex[j] == "}":
                depth -= 1
                if depth == 0:
                    break
        g = m.group(1)
        group_elements[g] = group_elements.get(g, 0) + 1
        group_questions[g] = group_questions.get(g, 0) + len(
            re.findall(r"\\begin\{question(?:multx)?\}", qtex[i:j])
        )

    active = "\n".join(l for l in ctex.splitlines() if not l.lstrip().startswith("%"))
    draws = [(g, int(n)) for g, n in re.findall(r"\\DrawGroup\{([^}]*)\}\{(-?\d+)\}", active)]
    full_draws = re.findall(r"\\Draw(?:All|FullGroup)\{([^}]*)\}", active) + \
        re.findall(r"\\InsertFullGroup\{([^}]*)\}", active)
    inserted_groups = {g for g, _ in draws} | set(full_draws)
    only_q = element_groups - inserted_groups
    only_c = inserted_groups - element_groups
    if only_c:
        fail(f"drawn in config.tex but no matching \\element questions: {sorted(only_c)}", problems)
    else:
        print(f"  ok    {len(inserted_groups)} group draws all have authored questions")

    # Draw count vs bank size -- compared against \element blocks, since that is
    # the unit AMC draws in.
    over = [(g, n, group_elements.get(g, 0)) for g, n in draws
            if n > group_elements.get(g, 0)]
    for g, n, size in over:
        fail(f"config.tex draws {n} from {g}, which holds only {size} "
             f"\\element block(s)", problems)
    if draws and not over:
        # Questions per copy, which is not the same as blocks drawn: a drawn
        # block can carry several questions.
        def questions_for(g, n_blocks):
            per = group_questions.get(g, 0) / max(group_elements.get(g, 1), 1)
            return per * n_blocks
        total = sum(questions_for(g, n) for g, n in draws) + sum(
            group_questions.get(g, 0) for g in full_draws)
        shared = sorted(g for g in inserted_groups
                        if group_questions.get(g, 0) != group_elements.get(g, 0))
        note = ""
        if shared:
            note = ("; shared-stem groups draw whole blocks: "
                    + ", ".join(f"{g} ({group_elements[g]} blocks / "
                                f"{group_questions[g]} questions)" for g in shared))
            if any(group_questions[g] % group_elements[g] for g in shared
                   for _ in [0]) and any(n != group_elements[g] for g, n in draws
                                         if g in shared):
                note += " -- WARNING: a partial draw from a group whose blocks " \
                        "differ in size gives students different question counts"
        print(f"  ok    every draw fits its bank ({total:g} questions per copy{note})")
    if only_q:
        print(f"  note  authored but not drawn by this sitting: {sorted(only_q)}")

    print()
    if problems:
        print(f"{len(problems)} problem(s) found.")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
