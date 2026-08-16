"""A browser front end for the batch half of the AMC workflow.

The desktop at :6080 is still the real AMC, and still the place to do the
interactive work — deciding what a half-filled box means, matching a smudged
student number to a name. This serves the other half, the part that is really
just "run a command and show me the output": edit questions, run the checkers,
compile, capture scans, mark, export.

Everything here shells out to the amc-* scripts in /usr/local/bin rather than
talking to AMC's databases directly, so the browser and the desktop stay in
agreement about how a project is built. Those scripts read the project's own
options.xml, which is the same file AMC's Preferences dialog writes.

Runs under uvicorn; see entrypoint.sh.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration. All of it comes from the same environment the desktop uses, so
# there is one source of truth for where the projects live.
# ---------------------------------------------------------------------------

REPO = Path(os.environ.get("AMC_REPO", "/work/repo")).resolve()
PROJECTS_DIR = Path(os.environ.get("AMC_PROJECTS_DIR", str(REPO / "exam"))).resolve()
# The port the *browser* must use to reach noVNC, which is the one Docker
# published on the host — not NOVNC_PORT, which is where websockify listens
# inside the container. Compose passes it in; fall back to the internal port
# for a bare `docker run` with no mapping.
NOVNC_PORT = os.environ.get(
    "AMC_NOVNC_PUBLIC_PORT", os.environ.get("NOVNC_PORT", "6080")
).strip()
TOKEN = os.environ.get("AMC_WEB_TOKEN", "").strip()
JOB_DIR = Path(os.environ.get("AMC_WEB_JOBDIR", "/tmp/amc-web-jobs"))
STATIC = Path(__file__).parent / "static"

# Files the editor is allowed to open and save. This is a text editor for a
# LaTeX question bank, not a file manager: anything that is not one of these is
# none of its business.
EDITABLE_SUFFIXES = {".tex", ".csv", ".txt", ".md"}
SCAN_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}

JOB_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AMC web", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Access control.
#
# This process runs shell commands on request, so it has no business being
# reachable from anywhere but the machine it runs on. The port is bound to
# 127.0.0.1 by default in docker-compose.yml; AMC_WEB_TOKEN adds a shared
# secret on top for when it is deliberately published (behind Tailscale, say).
# ---------------------------------------------------------------------------

def require_token(request: Request) -> None:
    if not TOKEN:
        return
    supplied = request.headers.get("x-amc-token") or request.query_params.get("token", "")
    # Constant-time: this is a bearer secret, and a timing-distinguishable
    # comparison is a real (if slow) way to guess one.
    if not secrets.compare_digest(supplied, TOKEN):
        raise HTTPException(status_code=401, detail="bad or missing token")


Guard = Depends(require_token)


# ---------------------------------------------------------------------------
# Paths.
#
# Every path that arrives from the browser is resolved and then checked to be
# inside the mounted repo. Without the resolve() first, "../../etc/passwd"
# passes a naive prefix test.
# ---------------------------------------------------------------------------

def safe_path(raw: str, root: Path = None) -> Path:
    root = (root or REPO).resolve()
    candidate = (root / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail=f"path is outside {root}")
    return candidate


def project_path(name: str) -> Path:
    # A project name is a relative path scanned up from PROJECTS_DIR — a bare
    # directory name in the single-repo layout, or "repo/project" once
    # PROJECTS_DIR spans several repos. Segment characters are restricted, but
    # a segment of only "." or ".." would still pass that check, so the
    # resolve()+containment check below is the real guard, same as safe_path.
    if not re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*", name or ""):
        raise HTTPException(status_code=400, detail="invalid project name")
    path = (PROJECTS_DIR / name).resolve()
    if PROJECTS_DIR not in path.parents:
        raise HTTPException(status_code=400, detail="invalid project name")
    if not path.is_dir():
        raise HTTPException(status_code=404, detail=f"no such project: {name}")
    return path


def git_repo_root(path: Path) -> Path:
    """The nearest .git above path, without leaving REPO. Falls back to REPO
    itself so a plain single-repo setup behaves exactly as before."""
    cur = path
    while True:
        if (cur / ".git").exists():
            return cur
        if cur == REPO or cur == cur.parent:
            return REPO
        cur = cur.parent


# ---------------------------------------------------------------------------
# Jobs.
#
# Compiling an exam takes tens of seconds and capture can take minutes, so
# every command runs as a background process writing to a log file, and the
# browser tails it. One job at a time per project: AMC's SQLite databases do
# not appreciate two processes writing at once.
# ---------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    kind: str
    project: str
    argv: list[str]
    log_path: Path
    started: float = field(default_factory=time.time)
    finished: float | None = None
    returncode: int | None = None
    process: Any = None
    log_handle: Any = None

    def poll(self) -> None:
        """Notice that the process has exited. Called on every status request.

        Deliberately not a background task: a task created inside one request
        belongs to that request's event loop, and reaping the process from
        there is how you end up with jobs that run forever because the loop
        that was going to notice them has gone away.
        """
        if self.returncode is not None or self.process is None:
            return
        code = self.process.poll()
        if code is not None:
            self.returncode = code
            self.finished = time.time()
            if self.log_handle and not self.log_handle.closed:
                self.log_handle.close()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "project": self.project,
            "command": " ".join(self.argv),
            "started": self.started,
            "finished": self.finished,
            "returncode": self.returncode,
            "running": self.returncode is None,
        }


JOBS: dict[str, Job] = {}
PROJECT_BUSY: dict[str, str] = {}   # project name -> running job id


async def start_job(kind: str, project: str, argv: list[str], cwd: Path | None = None) -> Job:
    busy_id = PROJECT_BUSY.get(project)
    busy = JOBS.get(busy_id) if busy_id else None
    if busy:
        busy.poll()
    if busy and busy.returncode is None:
        raise HTTPException(
            status_code=409,
            detail=f"{project} is already running {busy.kind} — wait for it to finish",
        )

    job_id = f"{int(time.time() * 1000):x}-{secrets.token_hex(3)}"
    log_path = JOB_DIR / f"{job_id}.log"
    job = Job(id=job_id, kind=kind, project=project, argv=argv, log_path=log_path)

    log = log_path.open("wb")
    log.write(f"$ {' '.join(argv)}\n\n".encode())
    log.flush()
    job.log_handle = log

    try:
        # stderr merged into stdout: the interesting output of a LaTeX failure
        # is interleaved across both, and splitting them scrambles the order.
        #
        # start_new_session puts the whole job in its own process group, which
        # is what makes cancelling work: killing amc-compile alone would leave
        # xelatex running and holding the project's files.
        job.process = subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd) if cwd else None,
            start_new_session=True,
        )
    except OSError as exc:
        log.close()
        raise HTTPException(status_code=500, detail=f"could not run {argv[0]}: {exc}")

    JOBS[job_id] = job
    PROJECT_BUSY[project] = job_id
    return job


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, offset: int = 0, _: None = Guard):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    job.poll()

    text, new_offset = "", offset
    if job.log_path.exists():
        with job.log_path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
            new_offset = handle.tell()
        text = chunk.decode("utf-8", errors="replace")

    return {**job.as_dict(), "log": text, "offset": new_offset}


@app.post("/api/jobs/{job_id}/cancel")
async def job_cancel(job_id: str, _: None = Guard):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    job.poll()
    if job.returncode is None and job.process:
        # Signal the process group, not just the wrapper script — see the
        # start_new_session note in start_job.
        try:
            os.killpg(os.getpgid(job.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            job.process.terminate()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Projects.
# ---------------------------------------------------------------------------

def run_status(project: Path, name: str | None = None) -> dict:
    """amc-status, as a dict. Never raises: a broken project still lists."""
    label = name or project.name
    try:
        out = subprocess.run(
            ["amc-status", str(project)],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            # amc-status only knows the leaf directory; a project found by the
            # recursive scan below needs the full relative path as its name, or
            # two projects sharing a leaf name (two repos each with a
            # "Midterm/") would collide in the picker and in every /api/projects/{name}/... route.
            data["name"] = label
            return data
        return {"name": label, "error": out.stderr.strip()[:400]}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        return {"name": label, "error": str(exc)}


# Directories that are never AMC projects themselves and are not worth
# descending into while scanning for ones nested inside them.
_SCAN_SKIP = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".cache",
    "dist", "build", "target", ".next", "questions",
}


_AMC_MARKER = re.compile(rb"\\element\s*\{|\\usepackage(?:\[[^\]]*\])?\{automultiplechoice\}|\\begin\{AMCform\}")


def _is_project_dir(path: Path) -> bool:
    if (path / "options.xml").exists():
        return True
    # Not-yet-initialized projects have no options.xml, so the fallback is a
    # .tex file — but scanning across ~/projects means that .tex file can
    # belong to an unrelated paper or another app's generated output, so it
    # only counts if it actually uses AMC's own LaTeX commands.
    return any(_AMC_MARKER.search(f.read_bytes()[:4096]) for f in path.glob("*.tex"))


def _scan_projects(root: Path, depth: int = 0, max_depth: int = 12) -> list[Path]:
    """Every AMC project under root, at any depth.

    Stops descending as soon as a project is found there — its own
    subfolders (data/, cr/, exports/...) are internals, not more projects.
    Symlinks are skipped outright to avoid loops from a repo that symlinks
    into another one.
    """
    found: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in _SCAN_SKIP:
            continue
        if _is_project_dir(entry):
            found.append(entry)
        elif depth < max_depth:
            found.extend(_scan_projects(entry, depth + 1, max_depth))
    return found


@app.get("/api/config")
async def get_config(_: None = Guard):
    return {
        "repo": str(REPO),
        "projects_dir": str(PROJECTS_DIR),
        "novnc_port": NOVNC_PORT,
        "token_required": bool(TOKEN),
    }


@app.get("/api/projects")
async def list_projects(_: None = Guard):
    if not PROJECTS_DIR.is_dir():
        raise HTTPException(status_code=500, detail=f"{PROJECTS_DIR} does not exist — check AMC_PROJECTS_SUBDIR")
    projects = [
        run_status(entry, entry.relative_to(PROJECTS_DIR).as_posix())
        for entry in sorted(_scan_projects(PROJECTS_DIR), key=lambda p: p.as_posix())
    ]
    return {"projects": projects}


@app.get("/api/projects/{name:path}/status")
async def project_status(name: str, _: None = Guard):
    return run_status(project_path(name), name)


class InitBody(BaseModel):
    engine: str = "pdflatex"
    texfile: str = "groups.tex"
    force: bool = False


@app.post("/api/projects/{name:path}/init")
async def project_init(name: str, body: InitBody, _: None = Guard):
    path = project_path(name)
    if body.engine not in {"pdflatex", "xelatex", "lualatex", "latex"}:
        raise HTTPException(status_code=400, detail="unsupported engine")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+\.tex", body.texfile):
        raise HTTPException(status_code=400, detail="texfile must be a .tex filename")

    argv = ["amc-project-init", "-e", body.engine, "-t", body.texfile]
    if body.force:
        argv.append("-f")
    argv.append(str(path))

    out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise HTTPException(status_code=400, detail=(out.stderr or out.stdout).strip())
    return {"ok": True, "output": out.stdout}


# ---------------------------------------------------------------------------
# The pipeline. Each of these is one amc-* script, run as a job.
# ---------------------------------------------------------------------------

class CompileBody(BaseModel):
    copies: int | None = None


@app.post("/api/projects/{name:path}/check")
async def project_check(name: str, _: None = Guard):
    path = project_path(name)
    job = await start_job("check", name, ["amc-check", str(path)])
    return job.as_dict()


@app.post("/api/projects/{name:path}/compile")
async def project_compile(name: str, body: CompileBody, _: None = Guard):
    path = project_path(name)
    argv = ["amc-compile"]
    if body.copies is not None:
        if not 1 <= body.copies <= 2000:
            raise HTTPException(status_code=400, detail="copies must be between 1 and 2000")
        argv += ["-n", str(body.copies)]
    argv.append(str(path))
    job = await start_job("compile", name, argv)
    return job.as_dict()


@app.post("/api/projects/{name:path}/capture")
async def project_capture(name: str, _: None = Guard):
    path = project_path(name)
    job = await start_job("capture", name, ["amc-capture", str(path)])
    return job.as_dict()


class MarkBody(BaseModel):
    associate: bool = True


@app.post("/api/projects/{name:path}/mark")
async def project_mark(name: str, body: MarkBody, _: None = Guard):
    path = project_path(name)
    argv = ["amc-mark"] + ([] if body.associate else ["-A"]) + [str(path)]
    job = await start_job("mark", name, argv)
    return job.as_dict()


class ExportBody(BaseModel):
    format: str = "csv"


@app.post("/api/projects/{name:path}/export")
async def project_export(name: str, body: ExportBody, _: None = Guard):
    path = project_path(name)
    if body.format not in {"csv", "ods", "list"}:
        raise HTTPException(status_code=400, detail="format must be csv, ods or list")
    job = await start_job("export", name, ["amc-export", "-f", body.format, str(path)])
    return job.as_dict()


# ---------------------------------------------------------------------------
# Files: the question bank, and the things the pipeline produces.
# ---------------------------------------------------------------------------

@app.get("/api/projects/{name:path}/files")
async def project_files(name: str, _: None = Guard):
    path = project_path(name)
    files = []

    def add(p: Path) -> None:
        if p.is_file() and p.suffix in EDITABLE_SUFFIXES:
            files.append({
                "path": str(p.relative_to(REPO)),
                "name": p.name,
                "size": p.stat().st_size,
                "modified": int(p.stat().st_mtime),
            })

    for p in sorted(path.glob("*.tex")):
        add(p)
    for p in sorted(path.glob("*.csv")):
        add(p)
    # The shared question bank lives beside the projects, not inside them, and
    # editing it is most of the reason this editor exists.
    questions = PROJECTS_DIR / "questions"
    if questions.is_dir():
        for p in sorted(questions.rglob("*.tex")):
            add(p)

    return {"files": files}


@app.get("/api/file")
async def read_file(path: str = Query(...), _: None = Guard):
    target = safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    if target.suffix not in EDITABLE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"not an editable file type: {target.suffix}")
    if target.stat().st_size > 4_000_000:
        raise HTTPException(status_code=413, detail="file is too large to edit here")
    return {
        "path": str(target.relative_to(REPO)),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "modified": int(target.stat().st_mtime),
    }


class WriteBody(BaseModel):
    path: str
    content: str
    modified: int | None = None


@app.put("/api/file")
async def write_file(body: WriteBody, _: None = Guard):
    target = safe_path(body.path)
    if target.suffix not in EDITABLE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"not an editable file type: {target.suffix}")
    if not target.parent.is_dir():
        raise HTTPException(status_code=400, detail="directory does not exist")

    # The same file is editable from the desktop, from the host, and from here.
    # If it changed underneath this editor since it was loaded, saving would
    # silently throw away the other edit.
    if body.modified is not None and target.exists():
        current = int(target.stat().st_mtime)
        if current > body.modified:
            raise HTTPException(
                status_code=409,
                detail="this file changed on disk since you opened it — reload before saving",
            )

    # Write to a neighbouring temp file and rename: a crash mid-write must not
    # leave a truncated question bank behind.
    tmp = target.with_suffix(target.suffix + ".amcweb-tmp")
    tmp.write_text(body.content, encoding="utf-8")
    os.replace(tmp, target)
    return {"ok": True, "modified": int(target.stat().st_mtime)}


@app.get("/api/projects/{name:path}/artifacts")
async def project_artifacts(name: str, _: None = Guard):
    path = project_path(name)
    out: dict[str, list] = {"documents": [], "exports": [], "scans": []}

    def describe(p: Path) -> dict:
        stat = p.stat()
        return {
            "path": str(p.relative_to(REPO)),
            "name": p.name,
            "size": stat.st_size,
            "modified": int(stat.st_mtime),
        }

    # amc-compiled.pdf is the intermediate AMC produces on its way to the
    # subject and correction PDFs; listing it invites printing the wrong file.
    for p in sorted(path.glob("*.pdf")):
        if p.name.startswith("amc-compiled"):
            continue
        out["documents"].append(describe(p))
    exports = path / "exports"
    if exports.is_dir():
        for p in sorted(exports.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                out["exports"].append(describe(p))
    scans = path / "scans"
    if scans.is_dir():
        for p in sorted(scans.iterdir()):
            if p.is_file() and p.suffix.lower() in SCAN_SUFFIXES:
                out["scans"].append(describe(p))
    return out


@app.get("/api/download")
async def download(path: str = Query(...), _: None = Guard):
    target = safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    return FileResponse(target, filename=target.name)


@app.post("/api/projects/{name:path}/scans")
async def upload_scans(name: str, files: list[UploadFile], _: None = Guard):
    path = project_path(name)
    scans = path / "scans"
    scans.mkdir(exist_ok=True)

    saved = []
    for upload in files:
        filename = os.path.basename(upload.filename or "")
        if not filename:
            continue
        suffix = Path(filename).suffix.lower()
        if suffix not in SCAN_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"{filename}: not a scan ({suffix})")
        # Never overwrite a scan already in the project — student work is the
        # one thing here that cannot be regenerated.
        target = scans / filename
        counter = 1
        while target.exists():
            target = scans / f"{Path(filename).stem}-{counter}{suffix}"
            counter += 1
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        saved.append(str(target.relative_to(REPO)))

    return {"saved": saved}


# ---------------------------------------------------------------------------
# Git.
# ---------------------------------------------------------------------------

@app.get("/api/git/status")
async def git_status(project: str = Query(default=""), _: None = Guard):
    root = git_repo_root(project_path(project)) if project else REPO
    try:
        porcelain = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=20,
        )
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {"available": False, "error": str(exc)}

    if porcelain.returncode != 0:
        return {"available": False, "error": porcelain.stderr.strip()}

    changes = [line for line in porcelain.stdout.splitlines() if line.strip()]
    return {
        "available": True,
        "branch": branch.stdout.strip(),
        "dirty": len(changes),
        "changes": changes[:100],
    }


class SyncBody(BaseModel):
    message: str = ""
    push: bool = True
    project: str = ""


@app.post("/api/git/sync")
async def git_sync(body: SyncBody, _: None = Guard):
    root = git_repo_root(project_path(body.project)) if body.project else REPO
    argv = ["amc-save", "-C", str(root)]
    if not body.push:
        argv.append("-n")
    if body.message.strip():
        argv += ["-m", body.message.strip()[:200]]
    job = await start_job("sync", f"__repo__:{root}", argv)
    return job.as_dict()


# ---------------------------------------------------------------------------
# The page itself.
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    page = STATIC / "index.html"
    if not page.is_file():
        return HTMLResponse("<h1>web/static/index.html is missing</h1>", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True, "projects_dir": str(PROJECTS_DIR)})


if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
