#!/usr/bin/env python3
"""Flag multiple-choice questions whose \\correctchoice gives itself away.

A student who has not studied should not be able to pick the right answer from
its *shape*. Three shapes leak it, and all three crept into this bank before:

  1. The correct choice is the longest option by a wide margin -- it is the one
     carrying the full explanation while the distractors are bare phrases.
  2. The correct choice is the only option containing a gloss: a parenthetical,
     a dash clause, or an "e.g.".
  3. Every distractor carries an absolute qualifier ("only", "never", "always",
     "must", "cannot", ...) and the correct choice carries none. Test-taking
     guides teach students to strike out absolutes on sight, so this hands the
     answer to someone who knows nothing about the topic. Writing distractors
     by overstating a true-ish statement is the natural way to fall into it.

Rule 3 exemption: some questions are *about* the overreach, so the absolutes
are the content rather than padding -- e.g. asking which scales of measurement
a mean is valid for, where "nominal only" is a real misconception worth
offering. Put a "% tell-ok:" comment with a reason inside such a question to
suppress the flag.

Usage:
    uv run tools/check_answer_tells.py [questions-dir]     # default: questions/

Exit code 0 = nothing flagged, 1 = at least one question flagged.
"""
import pathlib
import re
import sys

# Longest-by-margin threshold: flag when the correct choice is the longest AND
# beats the longest wrong choice by more than this many characters.
MARGIN = 12
GLOSS = re.compile(r"\(|--|e\.g\.")
# Words that make a statement unconditional. Kept to the ones a student is
# actually coached to strike out; vaguer hedges ("all", "any", "no") fire on
# ordinary prose ("all four columns", "no fixed schema") and are left out.
ABSOLUTE = re.compile(
    r"\b(only|never|always|must|cannot|exclusively|solely|entirely|impossible)\b",
    re.I,
)
# A "% tell-ok: <reason>" comment inside a question exempts it from rule 3.
TELL_OK = re.compile(r"%\s*tell-ok\b")


def clean(s):
    """Strip LaTeX markup that does not carry reading length for a student."""
    s = re.sub(r"\\(textbf|textit|emph|texttt)\{([^{}]*)\}", r"\2", s)
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    s = s.replace("{", "").replace("}", "").replace("$", "")
    return re.sub(r"\s+", " ", s).strip()


def main():
    qdir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "questions")
    files = sorted(qdir.glob("*.tex")) + sorted((qdir / "imported").glob("*.tex"))
    flagged = []
    n_checked = 0

    for f in files:
        # Disabled questions are commented-out blocks: skip them, but keep
        # "% tell-ok:" comments, which suppress a flag inside an active question.
        text = "\n".join(
            l for l in f.read_text(encoding="utf-8").splitlines()
            if not l.lstrip().startswith("%") or "tell-ok" in l
        )
        for _env, qid, body in re.findall(
            r"\\begin\{(question|questionmultx)\}\{([^}]*)\}(.*?)\\end\{\1\}", text, re.S
        ):
            correct = [clean(c) for c in re.findall(r"\\correctchoice\{(.*)\}", body)]
            wrong = [clean(c) for c in re.findall(r"\\wrongchoice\{(.*)\}", body)]
            if not correct or not wrong:
                continue
            n_checked += 1
            c, longest_wrong = correct[0], max(len(w) for w in wrong)
            if len(c) > longest_wrong + MARGIN:
                flagged.append(
                    (qid, f"correct choice is {len(c)} chars vs {longest_wrong} for the "
                          f"longest distractor")
                )
            elif GLOSS.search(c) and not any(GLOSS.search(w) for w in wrong):
                flagged.append((qid, "correct choice is the only one with a (…), --, or e.g. gloss"))
            elif (
                not ABSOLUTE.search(c)
                and all(ABSOLUTE.search(w) for w in wrong)
                and not TELL_OK.search(body)
            ):
                # Only fires when EVERY distractor has one and the correct
                # choice has none -- that is the shape a student can strike out
                # blind. A mix (some distractors absolute, some not) leaves no
                # usable pattern, so it is not flagged.
                words = sorted({m.group(0).lower() for w in wrong for m in ABSOLUTE.finditer(w)})
                flagged.append(
                    (qid, f"every distractor uses an absolute ({', '.join(words)}) "
                          f"and the correct choice uses none")
                )

    for qid, why in flagged:
        print(f"  FLAG  {qid}: {why}")
    print(f"\n{n_checked} multiple-choice questions checked, {len(flagged)} flagged.")
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
