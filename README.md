# AMC in a container, on the Mac mini

Auto Multiple Choice is a Perl/GTK3 desktop application that exists for Linux
only — there is no macOS build, which is why it will not install on the Mac
mini. This repository runs the **real AMC GUI** inside a Debian container with a
small XFCE desktop, and serves that desktop to a web browser.

You open `http://localhost:6080/`, and you get AMC itself: the project manager,
the LaTeX source editor, *Update documents*, *Data capture*, *Marking*,
*Reports*, the preferences dialog — all of it, unmodified. Nothing is
reimplemented, so nothing lags behind upstream AMC and no feature is missing.

Debian trixie ships `auto-multiple-choice` **1.7.0-3 built for arm64**, so this
runs natively on Apple Silicon — no emulation, no `--platform` flag, no Rosetta.
1.7.0 is the same version the 888102 exam bank was authored against.

---

## What you get

| You wanted | Where it is |
|---|---|
| Create a project | AMC's project manager, or `amc-project-init` for a folder cloned from git |
| Rebuild the PDFs | AMC → *Documents* → *Update documents* |
| Evaluate the scores | AMC → *Data capture* → *Marking* → *Reports* |
| Change AMC settings | AMC → *Edit* → *Preferences* (per-project settings in the project tabs) |
| Add / remove questions | Mousepad or the terminal on the desktop — or edit on the Mac, the files are the same |
| Save the project | It is already saved: the repo is bind-mounted, not copied |
| Pull / push to GitHub | **Save & Push to GitHub** on the desktop, or `git` in the terminal |

---

## Setup on the Mac mini

**1. Install a container runtime.** [OrbStack](https://orbstack.dev) is the
lighter, faster choice on Apple Silicon; Docker Desktop works identically.

**2. Clone this repo and the exam repo.**

```bash
git clone git@github.com:payakorn/amc-docker.git ~/projects/amc-docker
git clone git@github.com:payakorn/888102.git    ~/projects/888102
```

**3. Configure.**

```bash
cd ~/projects/amc-docker
cp .env.example .env
$EDITOR .env          # set EXAM_REPO, SSH_DIR, VNC_PASSWORD
```

`EXAM_REPO` must be an absolute path (`/Users/payakorn/projects/888102`) —
Compose does not expand `~`.

**4. Build and start.**

```bash
docker compose up -d --build
```

The first build pulls TeX Live and downloads roughly 2–3 GB; expect 10–20
minutes. Later starts take a couple of seconds.

**5. Open it.** <http://localhost:6080/> — password is whatever you set as
`VNC_PASSWORD`. AMC opens by itself.

For a smoother session than the browser gives (marking hundreds of papers, for
instance), macOS's built-in Screen Sharing talks to the same desktop:
`open vnc://localhost:5901`.

---

## Daily use

### Opening an existing project from the repo

AMC's project list shows the folders inside `EXAM_REPO/exam`. A project cloned
from git will **not** appear at first: AMC identifies a project by its
`options.xml`, which every one of these exam repos gitignores as generated
output. Give it one from the desktop terminal:

```bash
amc-project-init /work/repo/exam/888102-mt
```

Then reopen AMC's project list and pick `888102-mt`. Add `-e xelatex` for a Thai
exam, `-t some-other-file.tex` if the source is not `groups.tex`.

### Editing questions

The bank is plain LaTeX under `exam/questions/`, one file per chapter. Edit it
wherever you prefer — the container and the Mac see the same files:

- in the container: AMC's own *Edit source file* button, Mousepad, or `vim`
- on the Mac: VS Code, Claude Code, anything

Adding or removing questions changes a group's size, and `config.tex`'s
`\DrawGroup{group}{N}` must never ask for more than the group holds. Check
before compiling:

```bash
amc-check /work/repo/exam/888102-mt
```

That runs the exam repo's own three checkers (structure and draw counts,
answer-tells, table widths). It reads them from the repo, so it stays correct as
they change.

### Compiling and marking

Ordinary AMC, all of it inside the browser window:

1. *Documents* → **Update documents** → the DOC-\*.pdf files appear in the
   project folder, which is also a folder on the Mac — print them with Preview.
2. Scan the sat papers on the Mac, drop the PDF into the project's `scans/`.
3. *Data capture* → **Automatic** → point it at those files.
4. *Marking* → **Mark**, then *Reports* → export CSV/ODS.

Everything it writes lands in the mounted repo, so exports open in Numbers or
Excel on the Mac without moving anything.

### Saving and syncing

The working tree *is* the Mac's working tree — there is no separate copy to
save. To commit and sync, either double-click **Save & Push to GitHub** on the
desktop, or:

```bash
amc-save -m "add chapter 9 questions"
amc-save -n            # commit and pull, but do not push
```

Push uses the SSH keys mounted from the Mac's `~/.ssh` (read-only, copied in and
re-permissioned at startup, because SSH rejects keys that look world-readable).
If a key has a passphrase, run `amc-save` from the terminal so you can type it.

Note that `exam/.gitignore` deliberately excludes `data/`, `cr/`, `scans/`,
`*.sqlite` and `options.xml`. Scans and the capture/scoring databases stay on
the Mac mini and do not go to GitHub — that is intentional, they are large and
contain student work.

---

## Testing it

### After the first build

```bash
docker compose exec amc amc-selftest
```

`amc-selftest` is the honest test: it does not just check that things are
installed, it compiles a throwaway two-copy exam with AMC's command line, loads
the resulting layout into a database, and rasterizes the PDF — the same three
operations the GUI performs when you press *Update documents* and then capture
scans. It takes about half a minute and prints a pass/fail line per check:

```
Tool chain              auto-multiple-choice, pdflatex, xelatex, Xtigervnc,
                        websockify, automultiplechoice.sty, pgfplots, tgtermes
Desktop and remote      X on :1, XFCE running, noVNC on 6080, VNC on 5901
Wiring                  repo mounted and writable, git works, projects visible,
                        AMC preferences seeded, GitHub SSH authentication
Pipeline                AMC compiles a 2-copy exam, layout database builds,
                        ImageMagick can rasterize a PDF
```

Exit status is 0 only when every required check passes. `-q` skips the compile
and only reports what is installed. Warnings (missing Thai font, no SSH agent)
never fail the run.

Each failure line carries the fix. The two worth knowing in advance:
*"ImageMagick refuses to read PDF"* means the policy patch missed, so rebuild
with `--no-cache`; *"automultiplechoice.sty not on the LaTeX search path"* means
the kpathsea database is stale, same fix.

### Before rebuilding, on any machine

The scripts and the Compose file can be checked without Docker:

```bash
bash -n entrypoint.sh bin/*     # shell syntax
docker compose config           # resolves .env and validates the Compose file
```

### That the exam itself is still sound

Different question, different tool — `amc-check` runs the exam repo's own
checkers (structure and draw counts, answer-tells, table widths) and is what to
run after editing questions, before compiling:

```bash
docker compose exec amc amc-check /work/repo/exam/888102-mt
```

### End to end, by hand

The self-test proves the machinery; only you can confirm the exam is right.
Set `\NumCopies` to 2 in `config.tex`, press *Update documents*, and read
`DOC-sujet.pdf` — then print one copy, fill it in, scan it, and run it through
*Data capture* and *Marking*. That single round trip is what catches a
misconfigured answer sheet before 250 students sit the paper.

## Configuration

Everything lives in `.env`; see `.env.example` for the full list with comments.
The ones that matter most:

| Variable | Purpose |
|---|---|
| `EXAM_REPO` | Absolute path to the exam repo on the Mac. Required. |
| `AMC_PROJECTS_SUBDIR` | Folder inside it holding projects (`exam`, or `.`). |
| `VNC_PASSWORD` | Password for both noVNC and Screen Sharing. Change it. |
| `VNC_GEOMETRY` | Desktop size, e.g. `1920x1200`. |
| `BIND_ADDR` | `127.0.0.1` (default) keeps it on the Mac mini only. |
| `AMC_LATEX_ENGINE` | Default engine for new projects: `pdflatex` or `xelatex`. |
| `AMC_AUTOSTART` | `0` to get a bare desktop instead of AMC on launch. |

AMC's own preferences (window sizes, viewers, thresholds) live in the
`amc-home` named volume and survive `docker compose up --build`. They are seeded
once at first start and never overwritten afterwards, so anything changed in
*Preferences* sticks.

To work on a different course, point `EXAM_REPO` at that repo and restart:
`docker compose up -d`.

---

## Reaching it from off the Mac mini

The defaults bind to `127.0.0.1`, so the desktop is reachable only from the Mac
mini itself. VNC is unencrypted and noVNC here is plain HTTP, so do not simply
set `BIND_ADDR=0.0.0.0` and forward a port. Either:

- **Tailscale** — install on the Mac mini and on your laptop, then browse to
  `http://<mac-mini-tailscale-name>:6080/`. Simplest, and encrypted.
- **Cloudflare Tunnel** — put `cloudflared` in front of port 6080 with Access
  in front of it, if you want a real hostname.

---

## Fonts

Debian's `fonts-thai-tlwg` gives *TH Sarabun*, not the *TH Sarabun New* cut that
the Thai exam templates request by name. Drop the `.ttf` files into `fonts/` and
restart — they are mounted into the container's font path and `fc-cache` runs at
startup. See `fonts/README.md`.

---

## Troubleshooting

**Browser shows "Failed to connect to server".** The desktop is still starting,
or Xvnc died. `docker compose logs -f amc` — the entrypoint prints each step.

**Blank grey desktop, no AMC.** XFCE started but AMC did not. Open a terminal
from the desktop right-click menu and run `auto-multiple-choice` to see the
error.

**`dubious ownership` from git.** Should not happen (the entrypoint sets
`safe.directory '*'`), but if it does, the repo mount failed — check `EXAM_REPO`.

**LaTeX errors about a missing `.sty`.** The image installs
`texlive-latex-extra`, `-pictures`, `-fonts-extra`, `-xetex` and `-lang-other`,
which covers this bank. For anything beyond that, add the Debian package to the
TeX Live layer in the `Dockerfile` and rebuild.

**Data capture fails with "operation not allowed by the security policy".** That
is ImageMagick's PDF policy; the Dockerfile already relaxes it, so a failure here
means the build used a cached older layer — `docker compose build --no-cache`.

**A scan looks misread.** Nothing container-specific: AMC's *Data capture* page
lists pages by capture quality, and the zoom dialog fixes individual boxes.

---

## Why not a custom web UI

The first plan for this was a web application wrapping AMC's command-line tools
— project creation, a compile button, a scoring view, a settings form, a
question editor. That is a lot of surface area rebuilt worse: AMC's marking
workflow in particular (zooming into a doubtful box, correcting an association,
re-running the mark) is genuinely interactive and would have needed most of the
GUI recreated in the browser to be usable.

Shipping the actual GUI over noVNC gets all of it, immediately, and stays
correct when AMC updates. The parts a browser form really would have helped with
— editing questions, running the checkers, syncing git — are covered by the
editor, `amc-check` and `amc-save` on the same desktop.
