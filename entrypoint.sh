#!/bin/bash
# Bring up the desktop that AMC runs on, then hand it to the browser.
#
#   Xtigervnc :1       the X server, with its framebuffer exported as VNC
#   xfce4-session      window manager, panel, desktop icons
#   websockify         wraps VNC in WebSocket + serves noVNC's HTML client
#
# Everything runs in the foreground of this script; if any of the three dies,
# the container exits and Compose's restart policy brings it back.

set -euo pipefail

: "${VNC_PASSWORD:=amc2026}"   # >=6 chars: VNC password tools reject shorter
: "${VNC_GEOMETRY:=1600x1000}"
: "${VNC_DEPTH:=24}"
: "${NOVNC_PORT:=6080}"
: "${VNC_PORT:=5901}"
: "${AMC_PROJECTS_DIR:=/work/repo/exam}"
: "${AMC_AUTOSTART:=1}"

export HOME=/home/amc
export DISPLAY=:1
export XDG_RUNTIME_DIR=/tmp/runtime-amc
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

log() { printf '[amc-entrypoint] %s\n' "$*"; }

# ---------------------------------------------------------------------------
# VNC password.
#
# Set even when you only ever use the browser: macOS Screen Sharing (and most
# native VNC clients) refuse a server that offers no authentication, and this
# is what lets you connect with vnc://localhost:5901 instead of noVNC.
# ---------------------------------------------------------------------------
# Debian ships TigerVNC's password tool as tigervncpasswd; keep vncpasswd as a
# fallback in case the packaging changes back.
vncpass_tool=$(command -v tigervncpasswd || command -v vncpasswd || true)
[ -n "$vncpass_tool" ] || { log "no vncpasswd tool found"; exit 1; }

mkdir -p "$HOME/.vnc"
printf '%s\n' "$VNC_PASSWORD" | "$vncpass_tool" -f > "$HOME/.vnc/passwd"
chmod 600 "$HOME/.vnc/passwd"

# ---------------------------------------------------------------------------
# Git identity and SSH keys.
#
# ~/.ssh is bind-mounted read-only from the host at /ssh-host; it is copied in
# rather than used in place because SSH rejects keys whose permissions look too
# open, and a macOS bind mount does not reliably present 0600.
# ---------------------------------------------------------------------------
if [ -d /ssh-host ]; then
  mkdir -p "$HOME/.ssh"
  cp -r /ssh-host/. "$HOME/.ssh/" 2>/dev/null || true
  chmod 700 "$HOME/.ssh"
  find "$HOME/.ssh" -type f -exec chmod 600 {} + 2>/dev/null || true
  chmod 644 "$HOME/.ssh"/*.pub "$HOME/.ssh/known_hosts" 2>/dev/null || true
  log "copied SSH keys from host"
fi
ssh-keyscan -t rsa,ecdsa,ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
sort -u -o "$HOME/.ssh/known_hosts" "$HOME/.ssh/known_hosts" 2>/dev/null || true

# The repo is bind-mounted from the host, so its owner uid may not match this
# container's user; without this every git command fails "dubious ownership".
git config --global --add safe.directory '*' 2>/dev/null || true
if [ -n "${GIT_USER_NAME:-}" ];  then git config --global user.name  "$GIT_USER_NAME";  fi
if [ -n "${GIT_USER_EMAIL:-}" ]; then git config --global user.email "$GIT_USER_EMAIL"; fi
git config --global pull.rebase false 2>/dev/null || true

# ---------------------------------------------------------------------------
# AMC's global preferences, seeded once.
#
# Only written if absent, so anything changed in AMC's own Preferences dialog
# survives a restart (the home directory is a named volume).
# ---------------------------------------------------------------------------
mkdir -p "$HOME/.AMC.d/Models"
if [ ! -f "$HOME/.AMC.d/cf.default.xml" ]; then
  log "seeding AMC preferences (projects directory: $AMC_PROJECTS_DIR)"
  cat > "$HOME/.AMC.d/cf.default.xml" <<XML
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<global>
  <rep_projets>${AMC_PROJECTS_DIR}</rep_projets>
  <rep_modeles>${HOME}/.AMC.d/Models</rep_modeles>
  <defaut_moteur_latex_b>${AMC_LATEX_ENGINE:-pdflatex}</defaut_moteur_latex_b>
  <defaut_encodage_csv>UTF-8</defaut_encodage_csv>
  <defaut_encodage_liste>UTF-8</defaut_encodage_liste>
  <pdf_viewer>evince %f</pdf_viewer>
  <img_viewer>ristretto %f</img_viewer>
  <csv_viewer>mousepad %f</csv_viewer>
  <dir_opener>thunar %d</dir_opener>
  <html_browser>xdg-open %u</html_browser>
  <conserve_taille>1</conserve_taille>
</global>
XML
fi

# ---------------------------------------------------------------------------
# Desktop niceties: a shortcut to AMC on the desktop, and (by default) AMC
# launched automatically so the browser lands straight in the application.
# Delete ~/.config/autostart/amc.desktop, or set AMC_AUTOSTART=0, to get a
# plain desktop instead.
# ---------------------------------------------------------------------------
mkdir -p "$HOME/Desktop" "$HOME/.config/autostart"
# Debian names AMC's launcher net.auto_multiple_choice.amc.desktop; glob for it
# rather than hardcoding, so a rename upstream does not silently drop the icon.
for d in /usr/share/applications/*auto*multiple*choice*.desktop \
         /usr/share/applications/*amc*.desktop; do
  [ -f "$d" ] || continue
  cp -f "$d" "$HOME/Desktop/" 2>/dev/null || true
  chmod +x "$HOME/Desktop/$(basename "$d")" 2>/dev/null || true
  break
done
cat > "$HOME/Desktop/amc-sync.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Save & Push to GitHub
Comment=Commit everything in the exam repo, pull, then push
Icon=folder-remote
Exec=xfce4-terminal --hold --command amc-save
Terminal=false
DESKTOP
chmod +x "$HOME/Desktop/amc-sync.desktop"

cat > "$HOME/Desktop/exam-folder.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Exam Folder
Icon=folder-documents
Exec=thunar ${AMC_PROJECTS_DIR}
Terminal=false
DESKTOP
chmod +x "$HOME/Desktop/exam-folder.desktop"

if [ "$AMC_AUTOSTART" = "1" ]; then
  cat > "$HOME/.config/autostart/amc.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Auto Multiple Choice
Exec=auto-multiple-choice
X-GNOME-Autostart-enabled=true
DESKTOP
else
  rm -f "$HOME/.config/autostart/amc.desktop"
fi

# Fonts dropped into the mounted fonts/ directory (e.g. TH Sarabun New for the
# Thai exams) are only visible to XeLaTeX after the cache is rebuilt.
fc-cache -f >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Start everything.
# ---------------------------------------------------------------------------
cleanup() { log "shutting down"; kill $(jobs -p) 2>/dev/null || true; }
trap cleanup EXIT INT TERM

rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 2>/dev/null || true

vnc_server=$(command -v Xtigervnc || command -v Xvnc || true)
[ -n "$vnc_server" ] || { log "no VNC X server found"; exit 1; }

log "starting $(basename "$vnc_server") on :1 (${VNC_GEOMETRY}, rfb port ${VNC_PORT})"
"$vnc_server" :1 \
  -geometry "$VNC_GEOMETRY" \
  -depth "$VNC_DEPTH" \
  -rfbport "$VNC_PORT" \
  -rfbauth "$HOME/.vnc/passwd" \
  -SecurityTypes VncAuth \
  -AlwaysShared \
  -desktop "Auto Multiple Choice" \
  -localhost=0 &

# -localhost=0 matters for the published 5901 port: Docker forwards it through
# the bridge, so a native VNC client arrives from the gateway address, not from
# 127.0.0.1, and a localhost-only server would refuse it.

for _ in $(seq 1 50); do
  xdpyinfo -display :1 >/dev/null 2>&1 && break
  sleep 0.2
done
xdpyinfo -display :1 >/dev/null 2>&1 || { log "X server failed to start"; exit 1; }

log "starting XFCE session"
dbus-launch --exit-with-session xfce4-session &

log "serving noVNC on http://0.0.0.0:${NOVNC_PORT}/"
websockify --web=/usr/share/novnc "$NOVNC_PORT" "localhost:${VNC_PORT}" &

log "ready — open http://localhost:${NOVNC_PORT}/vnc.html (password: ${VNC_PASSWORD})"
wait -n
log "a service exited; stopping container"
