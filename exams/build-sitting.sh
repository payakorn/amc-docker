#!/bin/sh
# Build one sitting's AMC documents from the command line, in the order that
# works: s, meptex, b, s, meptex, b, k (one --mode per call).
#
# Run it INSIDE the amc container (it needs auto-multiple-choice and xelatex):
#   docker exec -it amc sh /work/repo/build-sitting.sh \
#       /work/repo/thai-exam/quiz [n-copies]
#
# Why two passes: the cover prints the question total with \total{...}, which
# reads the previous compile's .aux; the first pass prints "??". meptex must
# run before mode b, or AMC never reads the student-ID bubbles when marking.
# Never run this on a sitting that already holds scans you are marking: a
# rebuild changes the layout the scans were matched against.
set -eu
dir=${1:?usage: build-sitting.sh <sitting-folder> [n-copies]}
cd "$dir"
if [ -n "$(find cr -type f 2>/dev/null | head -1)" ]; then
    echo "refusing to rebuild: $dir/cr holds captured scans" >&2; exit 1
fi
n=${2:-$(sed -n 's/.*\\newcommand{\\NumCopies}{\([0-9]*\)}.*/\1/p' config.tex | head -1)}
: "${n:?could not read \\NumCopies from config.tex; pass it as the second argument}"
mkdir -p data cr scans exports
amc=auto-multiple-choice
prep() { $amc prepare --with xelatex --filter latex --source groups.tex --data ./data --prefix DOC- --n-copies "$n" "$@"; }
for pass in 1 2; do
    echo "== pass $pass: subject ($n copies)"; prep --mode s
    echo "== pass $pass: layout";  $amc meptex --src DOC-calage.xy --data ./data
    echo "== pass $pass: scoring"; prep --mode b
done
echo "== solutions"; prep --mode k --out-corrige-indiv DOC-indiv-solution.pdf
ls -l DOC-*.pdf
