# Extra fonts

Anything dropped in this folder is mounted read-only at
`/usr/local/share/fonts/custom` and registered with `fc-cache` when the
container starts, which makes it available to XeLaTeX (`\setmainfont`) and to
the desktop.

Put `TH Sarabun New` (`THSarabunNew*.ttf`) here for the Thai exams — the image
ships Debian's `fonts-thai-tlwg`, which provides *TH Sarabun*, not the *New*
cut that `\setmainfont{TH Sarabun New}` asks for by name.

The `.ttf`/`.otf` files themselves are gitignored — licensing varies, and they
do not belong in this repository.
