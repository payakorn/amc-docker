#!/usr/bin/env python3
"""Pre-compile sanity checks for one sitting of an AMC exam project.

Usage (from the exam project folder, e.g. thai-exam/):
    uv run tools/check_questions.py <sitting>     # e.g. beforemidterm, midterm, prefinal, final
    for d in beforemidterm midterm prefinal final; do uv run tools/check_questions.py $d; done

Only active questions are checked: a question is disabled by commenting out its
whole \\element block, so comment lines are dropped before parsing.

Checks:
  - balanced \\begin/\\end{question} and \\begin/\\end{choices}
  - every multiple-choice question has exactly 4 choices and exactly 1
    \\correctchoice (open questions, with \\AMCOpen, are skipped)
  - no duplicate question IDs across all chapter files
  - no unescaped % or & inside question bodies
  - the layout (groups.tex and the files it \\input's) loads every file under questions/
  - every \\SelectTopic group in config.tex exists and has at least as many
    active questions as it draws (more makes \\copygroup reuse questions and
    AMC fails with "question ID used several times for the same paper");
    \\SelectTopic{group} without a count copies the whole group, and then
    \\NumMCQQuestions, if config.tex sets it, must not exceed the pool
Also prints the active question count of every group, which is the number to
use in config.tex.

Stdlib only. Exit code 0 = all clean, 1 = problems found.
"""
import pathlib
import re
import sys


def fail(msg, problems):
    problems.append(msg)
    print(f"  FAIL  {msg}")


def active(text):
    """Drop comment lines (disabled questions and notes)."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("%"))


def layout_text(path, seen=None):
    """groups.tex plus every file it \\input's that exists (e.g. ../common/exam.tex)."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen or not path.is_file():
        return ""
    seen.add(path)
    text = active(path.read_text(encoding="utf-8"))
    parts = [text]
    for ref in re.findall(r"\\input\{([^}]*)\}", text):
        if "questions/" in ref:
            continue
        child = path.parent / ref
        if child.suffix != ".tex":
            child = child.with_suffix(".tex")
        parts.append(layout_text(child, seen))
    return "\n".join(parts)


def main():
    folder = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    qdir = folder / "questions"
    if not qdir.is_dir():
        qdir = folder.parent / "questions"  # shared bank one level up
    qfiles = sorted(qdir.glob("*.tex"))
    qtex = "\n".join(active(f.read_text(encoding="utf-8")) for f in qfiles)
    ltex = layout_text(folder / "groups.tex")
    ctex = active((folder / "config.tex").read_text(encoding="utf-8"))
    problems = []

    for env in ("question", "choices"):
        b = len(re.findall(rf"\\begin\{{{env}\}}", qtex))
        e = len(re.findall(rf"\\end\{{{env}\}}", qtex))
        if b != e:
            fail(f"{env}: {b} \\begin vs {e} \\end", problems)
        else:
            print(f"  ok    {env}: {b} balanced pairs")

    ids = []
    blocks = re.findall(r"\\begin\{question\}\{([^}]*)\}(.*?)\\end\{question\}", qtex, re.S)
    for qid, body in blocks:
        ids.append(qid)
        if "\\AMCOpen" in body:
            continue
        n_correct = len(re.findall(r"\\correctchoice", body))
        n_wrong = len(re.findall(r"\\wrongchoice", body))
        if n_correct != 1:
            fail(f"{qid}: {n_correct} \\correctchoice (want 1)", problems)
        if n_correct + n_wrong != 4:
            fail(f"{qid}: {n_correct + n_wrong} choices (want 4)", problems)
        for ch in ("%", "&"):
            if re.search(rf"(?<!\\){re.escape(ch)}", body):
                fail(f"{qid}: unescaped '{ch}' in question body", problems)
    print(f"  ok    {len(blocks)} active questions parsed across {len(qfiles)} files")

    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        fail(f"duplicate question IDs: {dupes}", problems)
    else:
        print(f"  ok    {len(ids)} unique question IDs")

    input_files = set(re.findall(r"\\input\{(?:\.\./)?questions/([\w.-]+?)(?:\.tex)?\}", ltex))
    actual_files = {f.stem for f in qfiles}
    if input_files != actual_files:
        if actual_files - input_files:
            fail(f"questions/ files the layout never \\input's: {sorted(actual_files - input_files)}", problems)
        if input_files - actual_files:
            fail(f"the layout \\input's missing files: {sorted(input_files - actual_files)}", problems)
    else:
        print(f"  ok    layout inputs all {len(actual_files)} question files")

    counts = {}
    for g in re.findall(r"\\element\{([^}]*)\}", qtex):
        counts[g] = counts.get(g, 0) + 1
    draws = re.findall(r"\\SelectTopic\{([^}]*)\}(?:\{(\d+)\})?", ctex)
    drawn = {}
    for g, n in draws:
        drawn[g] = drawn.get(g, 0) + (int(n) if n else counts.get(g, 0))
    for g, n in drawn.items():
        if g not in counts:
            fail(f"\\SelectTopic{{{g}}} in config.tex has no active questions", problems)
        elif n > counts[g]:
            fail(f"\\SelectTopic{{{g}}} draws {n} but only {counts[g]} are active", problems)
    pool = sum(drawn.values())
    per_copy = re.search(r"\\newcommand\{\\NumMCQQuestions\}\{(\d+)\}", ctex)
    if per_copy and int(per_copy.group(1)) > pool:
        fail(f"\\NumMCQQuestions is {per_copy.group(1)} but the selected pool has {pool}", problems)
    elif per_copy:
        print(f"  ok    {len(drawn)} groups selected, {per_copy.group(1)} of {pool} questions per copy")
    else:
        print(f"  ok    {len(drawn)} groups drawn, {pool} questions per copy")

    print("\n  group                 active  drawn")
    for g in sorted(counts):
        print(f"  {g:<22}{counts[g]:>6}  {drawn.get(g, '-'):>5}")

    print()
    if problems:
        print(f"{len(problems)} problem(s) found.")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
