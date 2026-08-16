# AMC in a container, on the Mac mini

Auto Multiple Choice is a Perl/GTK3 desktop application that exists for Linux
only — there is no macOS build, which is why it will not install on the Mac
mini. This repository runs the **real AMC GUI** inside a Debian container with a
small XFCE desktop, and puts two front doors on it.

| | | |
|---|---|---|
| **<http://localhost:6090/>** | the web interface | edit questions, run the checkers, compile, capture scans, mark, export |
| **<http://localhost:6091/>** | the AMC desktop | all of AMC, unmodified — and the only place to review ambiguous boxes and match papers to students |

Most of a working session is the first one: write a question, check it, rebuild
the PDF, look at it. That is a form and a log, and a browser tab does it better
than a remote desktop does. The moment the work becomes *looking at a scanned
image and deciding what it says*, the desktop is the right tool, and the web
interface says so and links to it.

Debian trixie ships `auto-multiple-choice` **1.7.0-3 built for arm64**, so this
runs natively on Apple Silicon — no emulation, no `--platform` flag, no Rosetta.
1.7.0 is the same version the 888102 exam bank was authored against.

---

## What you get

| You wanted | Where it is |
|---|---|
| Add / remove questions | The editor in the web interface — or VS Code on the Mac, the files are the same |
| Check the bank before compiling | **Check** in the web interface (`amc-check`) |
| Rebuild the PDFs | **Update documents** in the web interface, or AMC → *Documents* |
| Read in scanned papers | Drop them on the web interface, then **Capture** |
| Compute the marks, export a CSV | **Mark** and **Export** in the web interface |
| **Fix a box AMC could not read** | **The desktop.** AMC → *Data capture* → the zoom dialog |
| **Match a paper to a student** | **The desktop.** AMC's association dialog |
| Change AMC settings | AMC → *Edit* → *Preferences* (the web interface reads them, never writes them) |
| Create a project | AMC's project manager, or **Create project** in the web interface for a folder cloned from git |
| Save the project | It is already saved: the repo is bind-mounted, not copied |
| Pull / push to GitHub | **Sync** in the web interface, **Save & Push to GitHub** on the desktop, or `git` in a terminal |

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

**5. Open it.** <http://localhost:6090/> for the web interface. The desktop is
at <http://localhost:6091/>, password `VNC_PASSWORD`, and the web interface's
**Desktop ↗** button opens it for you.

For a smoother desktop session than the browser gives (marking hundreds of
papers, for instance), macOS's built-in Screen Sharing talks to the same
desktop: `open vnc://localhost:6092`.

---

## Daily use

### Opening an existing project from the repo

Both front doors list the folders inside `EXAM_REPO/exam`. A project cloned from
git will show as **not set up**: AMC identifies a project by its `options.xml`,
which every one of these exam repos gitignores as generated output. The web
interface offers a **Create project** button for exactly this (pick `xelatex`
for a Thai exam); from a terminal it is:

```bash
amc-project-init /work/repo/exam/888102-mt
```

### Editing questions

The bank is plain LaTeX under `exam/questions/`, one file per chapter. Edit it
wherever you prefer — all three views are the same files on disk:

- the web interface's editor (`Ctrl`+`S` saves; it refuses to save over a file
  that changed underneath it)
- the desktop: AMC's own *Edit source file* button, Mousepad, or `vim`
- the Mac: VS Code, Claude Code, anything

Adding or removing questions changes a group's size, and `config.tex`'s
`\DrawGroup{group}{N}` must never ask for more than the group holds. **Check**
runs the exam repo's own three checkers (structure and draw counts,
answer-tells, table widths) and is what catches this. `Ctrl`+`Enter` runs the
checkers and then compiles only if they pass.

### Compiling, capturing, marking

In the web interface, top to bottom:

1. **Update documents** → `DOC-sujet.pdf` and `DOC-corrige.pdf` appear in the
   project folder, which is also a folder on the Mac. Download or print them.
2. Scan the sat papers on the Mac, drop the PDF onto the web interface.
3. **Capture** → AMC extracts one image per page and reads every box.
4. **Mark** → scores, plus automatic association if the project has a student
   list configured.
5. **Export** → CSV, ODS or a PDF list, written to the project's `exports/`.

Everything lands in the mounted repo, so exports open in Numbers or Excel on the
Mac without moving anything.

### The part that stays on the desktop

After capture, the web interface shows a **Needs the desktop** panel when there
is something only a person can settle:

- **ambiguous boxes** — a box whose darkness sits too close to the threshold to
  call. AMC's *Data capture* page lists pages by quality and its zoom dialog
  fixes individual boxes.
- **unmatched papers** — a student number the scanner smudged or the student
  overwrote. AMC's association dialog shows the scanned name field next to the
  student list.

Both are "look at this image and decide", which is why they are not buttons
here. Click **Desktop ↗**, fix them in AMC, and the web interface picks up the
new state on its next refresh — it is the same project directory either way.

### Saving and syncing

The working tree *is* the Mac's working tree — there is no separate copy to
save. **Sync** in the web interface commits, pulls and pushes; so does
**Save & Push to GitHub** on the desktop, and so does:

```bash
amc-save -m "add chapter 9 questions"
amc-save -n            # commit and pull, but do not push
```

Push uses the SSH keys mounted from the Mac's `~/.ssh` (read-only, copied in and
re-permissioned at startup, because SSH rejects keys that look world-readable).
If a key has a passphrase, run `amc-save` from a terminal so you can type it.

Note that `exam/.gitignore` deliberately excludes `data/`, `cr/`, `scans/`,
`*.sqlite` and `options.xml`. Scans and the capture/scoring databases stay on
the Mac mini and do not go to GitHub — that is intentional, they are large and
contain student work.

---

## The command line, if you prefer it

The web interface is a thin front end over these; they work the same from a
terminal, on the desktop or through `docker compose exec amc`:

| Command | Does |
|---|---|
| `amc-check <project>` | the exam repo's own pre-compile checkers |
| `amc-compile <project>` | *Update documents*: compile, extract scoring, load the layout |
| `amc-capture <project> [files…]` | extract page images and read the boxes |
| `amc-mark <project>` | apply the scoring rules, then associate |
| `amc-export -f csv <project>` | write the marks out |
| `amc-status <project> --text` | where this project stands |
| `amc-project-init <dir>` | give a cloned folder its `options.xml` |
| `amc-save` | commit, pull, push |

All of them read the project's own `options.xml` for the engine, source file,
thresholds and mark scale — the same file AMC's *Preferences* dialog writes.
Change a threshold in the GUI and the next `amc-mark` uses it.

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
                        websockify, the amc-* wrappers, the web interface's
                        dependencies, automultiplechoice.sty, pgfplots, tgtermes
Desktop and remote      X on :1, XFCE running, noVNC on 6080, VNC on 5901,
                        the web interface answering on 8080
                        (the ports inside the container, not the published ones)
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
python3 -m py_compile bin/amc-opts bin/amc-status web/app.py
docker compose config           # resolves .env and validates the Compose file
```

### That the exam itself is still sound

Different question, different tool — **Check** in the web interface (or
`amc-check`) runs the exam repo's own checkers and is what to run after editing
questions, before compiling.

### End to end, by hand

The self-test proves the machinery; only you can confirm the exam is right.
Set `\NumCopies` to 2 in `config.tex`, press **Update documents**, and read
`DOC-sujet.pdf` — then print one copy, fill it in, scan it, and run it through
**Capture** and **Mark**. That single round trip is what catches a misconfigured
answer sheet before 250 students sit the paper.

---

## Configuration

Everything lives in `.env`; see `.env.example` for the full list with comments.
The ones that matter most:

| Variable | Purpose |
|---|---|
| `EXAM_REPO` | Absolute path to the exam repo on the Mac. Required. |
| `AMC_PROJECTS_SUBDIR` | Folder inside it holding projects (`exam`, or `.`). |
| `VNC_PASSWORD` | Password for the desktop and Screen Sharing. Change it. |
| `VNC_GEOMETRY` | Desktop size, e.g. `1920x1200`. |
| `BIND_ADDR` | `127.0.0.1` (default) keeps everything on the Mac mini only; `0.0.0.0` opens both front doors to the LAN. |
| `WEB_PORT` | Web interface port on the host, default `6090`. `NOVNC_PORT` (`6091`) and `VNC_PORT` (`6092`) alongside it. |
| `AMC_WEB` | `0` to run the desktop alone, without the web interface. |
| `AMC_WEB_TOKEN` | Shared secret for the web interface. See below. |
| `AMC_LATEX_ENGINE` | Default engine for new projects: `pdflatex` or `xelatex`. |
| `AMC_AUTOSTART` | `0` to get a bare desktop instead of AMC on launch. |

AMC's own preferences (window sizes, viewers, thresholds) live in the
`amc-home` named volume and survive `docker compose up --build`. They are seeded
once at first start and never overwritten afterwards, so anything changed in
*Preferences* sticks — and the web interface reads those settings rather than
keeping its own.

To work on a different course, point `EXAM_REPO` at that repo and restart:
`docker compose up -d`.

---

## Reaching it from off the Mac mini

The defaults bind to `127.0.0.1`, so both front doors are reachable only from
the Mac mini itself.

### On the same network (LAN)

For a co-teacher's laptop in the same office, or your own laptop on the same
wifi, one line in `.env`:

```dotenv
BIND_ADDR=0.0.0.0
```

Then `docker compose up -d`, and browse from the other machine to
`http://<mac-mini-ip>:6090/` for the web interface and
`http://<mac-mini-ip>:6091/vnc.html` for the desktop. Find that address with
`ipconfig getifaddr en0` on macOS, `hostname -I` on Linux; the machine's
`.local` name (`http://mac-mini.local:6090/`) usually works too.

**Which port to open.** Three are published, and they are not equally needed:

| Port | What | Open it? |
|---|---|---|
| `6090` | the web interface | **Yes** — this is the one. The whole batch workflow lives here, and it is the only port a colleague needs. |
| `6091` | noVNC, the desktop in a browser | Only if someone other than you has to fix ambiguous boxes or match papers. |
| `6092` | raw VNC, for Screen Sharing | Rarely. Unencrypted, and 6091 already reaches the same desktop. |

To open just the web interface, drop the other two `ports:` lines from
`docker-compose.yml` — the services keep running inside the container, they
simply stop being reachable, and the **Desktop ↗** button then only works from
the Mac mini itself.

Two things happen automatically once `BIND_ADDR` is not loopback, because **the
web interface runs commands and writes to the exam repo**:

- **A token is required.** If `AMC_WEB_TOKEN` is empty, the container generates
  one at first start, keeps it in the `amc-home` volume so it survives
  restarts, and prints it — `docker compose logs amc | grep token`. Each
  browser is asked for it once and remembers it. Set `AMC_WEB_TOKEN` in `.env`
  to choose your own instead.
- **The startup log warns** if `VNC_PASSWORD` is still the example value.

To publish on one interface only — the wired LAN, say, but not a guest
network — put that address in `BIND_ADDR` instead of `0.0.0.0`. If the Mac
mini's firewall is on (*System Settings → Network → Firewall*), allow incoming
connections for Docker.

None of this is encrypted: plain HTTP, and VNC's own weak authentication. It is
fine on a network you trust, and not fine on conference wifi or the open
internet.

### From anywhere else

- **Tailscale** — install on the Mac mini and on your laptop, then browse to
  `http://<mac-mini-tailscale-name>:6090/`. Encrypted, and no ports exposed to
  the LAN at all; leave `BIND_ADDR=0.0.0.0` and let Tailscale's ACLs do the
  gatekeeping.
- **Cloudflare Tunnel** — put `cloudflared` in front of the ports with Access in
  front of it, if you want a real hostname and TLS.

Do not port-forward 6090 or 6091 from the router. Anyone who reaches port 6090
with the token can run commands as the container user and rewrite the exam
repo; port 6092 is unencrypted VNC.

---

## Fonts

Debian's `fonts-thai-tlwg` gives *TH Sarabun*, not the *TH Sarabun New* cut that
the Thai exam templates request by name. Drop the `.ttf` files into `fonts/` and
restart — they are mounted into the container's font path and `fc-cache` runs at
startup. See `fonts/README.md`.

---

## Troubleshooting

**Web interface shows nothing / cannot connect.** `docker compose logs -f amc` —
the entrypoint prints each service as it starts. `AMC_WEB=0` in `.env` turns it
off entirely, which is the first thing to check.

**Browser shows "Failed to connect to server" on the desktop port.** The desktop is still
starting, or Xvnc died. Same log.

**Blank grey desktop, no AMC.** XFCE started but AMC did not. Open a terminal
from the desktop right-click menu and run `auto-multiple-choice` to see the
error.

**A button in the web interface fails.** The console at the bottom holds the
command that ran and everything it printed — the same output you would have got
in a terminal. LaTeX errors in particular are quoted in full.

**"is already running" when starting a job.** One job at a time per project, on
purpose: AMC's SQLite databases do not tolerate two writers. Wait, or press
**Stop**.

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

## Why the split, and not one or the other

The first plan for this was noVNC alone: ship the real GUI, get every feature
immediately, stay correct when AMC updates. That reasoning still holds for the
half of AMC that is genuinely interactive. Marking in particular — zooming into a
doubtful box, correcting an association, re-running the mark — would need most of
the GUI recreated in the browser to be usable, and a worse copy of it costs real
time when there are 250 papers to get through.

But it was the wrong answer for the other half. Writing questions, running the
checkers, compiling, exporting a CSV: none of that is spatial, none of it needs a
window manager, and doing it through a remote framebuffer is strictly worse than
a text box and a log pane — slower to type in, blurry, awkward from a laptop.

So the web interface covers the batch steps and nothing else. It has no settings
of its own, stores no state, and reimplements no AMC logic: every button shells
out to the same `amc-*` scripts documented above, which read the project's
`options.xml` for their parameters. When it reaches something that needs a human
eye on an image, it says so and hands over. That is why it cannot drift from
AMC — there is nothing in it to drift.
