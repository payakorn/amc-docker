# Auto Multiple Choice, as a browser-accessible Linux desktop.
#
# AMC is a Perl/GTK3 application that only exists for Linux — there is no
# macOS build, which is why it will not install on the Mac mini. This image
# runs the real AMC GUI inside a small XFCE desktop, exports that desktop over
# VNC, and serves a noVNC web client on port 6080, so the whole thing is usable
# from Safari/Chrome on macOS with nothing installed on the Mac but Docker.
#
# Debian trixie ships auto-multiple-choice 1.7.0-3 built for arm64, so this
# runs natively on Apple Silicon — no emulation, no --platform flag.

FROM debian:trixie-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG UID=1000
ARG GID=1000

# ---------------------------------------------------------------------------
# 1. AMC itself.
#
# Installed on its own layer WITH recommends: the Debian package pulls in the
# Perl/GTK stack, OpenCV helpers and the LaTeX pieces it needs through them,
# and stripping recommends here is how you end up with a GUI that starts but
# cannot compile or read a scan.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y auto-multiple-choice \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. LaTeX.
#
# Enough of TeX Live to compile this course's exam bank (tikz + pgfplots for
# the chart questions, multicol, tgtermes, amsmath) and the Thai exams from the
# amc-exam skill (xelatex + Thai hyphenation/fonts).
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      texlive-latex-base \
      texlive-latex-recommended \
      texlive-latex-extra \
      texlive-pictures \
      texlive-fonts-recommended \
      texlive-fonts-extra \
      texlive-xetex \
      texlive-lang-other \
      texlive-plain-generic \
      lmodern \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 3. Desktop + remote access + the tools the workflow needs around AMC.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      # minimal XFCE session (a full desktop is not needed, but AMC's dialogs
      # want a window manager, a panel and a place to put files)
      xfce4-session xfwm4 xfdesktop4 xfce4-panel xfce4-appfinder \
      xfce4-terminal thunar mousepad ristretto evince \
      dbus-x11 x11-xserver-utils xdg-utils \
      # remote display: Xvnc serves :1, websockify serves noVNC's web client.
      # The websockify command comes from python3-websockify — Debian's
      # "websockify" binary package ships only the rebind helper.
      tigervnc-standalone-server tigervnc-common \
      novnc python3-websockify \
      # question editing, version control, and moving files in and out
      git openssh-client ca-certificates curl wget \
      nano vim-tiny less zip unzip rsync \
      # the exam repo's checker scripts (stdlib-only Python)
      python3 \
      # AMC shells out to these for scan handling and PDF assembly
      poppler-utils imagemagick ghostscript \
      procps locales fonts-dejavu fonts-liberation fonts-thai-tlwg \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 4. Fixes that only bite at runtime.
# ---------------------------------------------------------------------------

# Debian's ImageMagick ships a policy that forbids reading/writing PDF. AMC
# converts scanned PDFs to page images, so leaving this in place produces an
# "attempt to perform an operation not allowed by the security policy" failure
# halfway through data capture.
RUN set -eux; \
    for f in /etc/ImageMagick-*/policy.xml; do \
      [ -f "$f" ] || continue; \
      sed -i 's/rights="none" pattern="PDF"/rights="read|write" pattern="PDF"/' "$f"; \
      sed -i 's/rights="none" pattern="PS"/rights="read|write" pattern="PS"/' "$f"; \
    done

# automultiplechoice.sty is installed by auto-multiple-choice-common into
# /usr/share/texmf, which was populated before TeX Live arrived; refresh the
# kpathsea file database so \usepackage{automultiplechoice} resolves.
RUN mktexlsr || texhash || true

RUN sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    DISPLAY=:1

# noVNC's landing page: Debian installs vnc.html but no index.html, so
# http://host:6080/ would 404 without this.
RUN [ -e /usr/share/novnc/index.html ] || ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# uv, because every Python entry point in this course's repos is "uv run".
# Non-fatal: the exam checkers are stdlib-only and fall back to python3.
RUN (curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh) \
 || echo "uv install skipped — checkers will run under python3"

# ---------------------------------------------------------------------------
# 5. The user the desktop runs as.
#
# uid/gid default to 1000 so files written into the bind-mounted repo keep
# sane ownership on a Linux host; on macOS Docker Desktop maps this for you.
# ---------------------------------------------------------------------------
RUN set -eux; \
    if getent group "$GID" >/dev/null; then groupmod -n amc "$(getent group "$GID" | cut -d: -f1)"; \
    else groupadd -g "$GID" amc; fi; \
    if getent passwd "$UID" >/dev/null; then \
      usermod -l amc -d /home/amc -m -s /bin/bash "$(getent passwd "$UID" | cut -d: -f1)"; \
    else useradd -m -u "$UID" -g "$GID" -s /bin/bash amc; fi; \
    mkdir -p /home/amc /work; \
    chown -R "$UID:$GID" /home/amc /work

COPY bin/ /usr/local/bin/
COPY entrypoint.sh /usr/local/bin/amc-entrypoint
RUN chmod +x /usr/local/bin/amc-entrypoint /usr/local/bin/amc-*

USER amc
WORKDIR /work
ENV HOME=/home/amc

EXPOSE 6080 5901

ENTRYPOINT ["/usr/local/bin/amc-entrypoint"]
