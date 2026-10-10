#!/usr/bin/env bash
# Install or update HOOD inside WSL2 (Ubuntu) from the Windows copy of the repository.
#
#   bash install_hood_wsl.sh /mnt/c/Users/<you>/Documents/dracula [--start] [--port 8999]
#
# What it does (nothing else):
#   1. copies the code to ~/hood (Linux disk: fast, and file permissions work; your HOOD data,
#      vault and audit stay in ~/hood/artifacts and ~/.hood and are never overwritten)
#   2. creates ~/hood/.venv and installs requirements.txt
#   3. checks that the agent sandbox (unshare -rn) works, and tells you how to fix it if not
#   4. with --start: runs HOOD on 127.0.0.1:<port>; Chrome on Windows opens the same address
# It never uses sudo by itself; when something needs it, it prints the exact command for you.
set -euo pipefail

SRC="${1:-}"
shift || true
START=0
PORT=8999
while [ $# -gt 0 ]; do
  case "$1" in
    --start) START=1 ;;
    --port) PORT="$2"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
DEST="${HOOD_WSL_DIR:-$HOME/hood}"

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ -n "$SRC" ] || fail "Give the Windows HOOD folder, e.g. /mnt/c/Users/zack/Documents/dracula"
[ -f "$SRC/hood_cli.py" ] || fail "$SRC does not look like the HOOD folder (no hood_cli.py)"
grep -qi microsoft /proc/version 2>/dev/null || echo "Note: this does not look like WSL; continuing anyway."

say "1/4 Copying HOOD code to $DEST"
mkdir -p "$DEST"
# Code only: never copy the Windows virtualenv, caches, or HOOD's data/vault/audit folders.
tar -C "$SRC" --exclude=./.venv --exclude=./artifacts --exclude=./.git --exclude='__pycache__' \
    --exclude=./.pytest_cache --exclude=./node_modules -cf - . | tar -C "$DEST" -xf -
# Windows editors may save CRLF line endings; Linux shell scripts need LF.
find "$DEST/scripts" -name '*.sh' -exec sed -i 's/\r$//' {} +
if [ -f "$SRC/.env" ] && [ ! -f "$DEST/.env" ]; then
  cp "$SRC/.env" "$DEST/.env" && chmod 600 "$DEST/.env" && echo "Copied your .env settings."
fi

say "2/4 Python and packages"
PY=""
for candidate in python3.13 python3.12; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  fail "Python 3.12 or 3.13 is missing. Run:  sudo apt update && sudo apt install -y python3 python3-venv  then run this again."
fi
if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  fail "The venv module is missing. Run:  sudo apt update && sudo apt install -y $PY-venv  then run this again."
fi
echo "Using $($PY --version)"
if [ ! -x "$DEST/.venv/bin/python" ]; then
  "$PY" -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DEST/.venv/bin/python" -m pip install --quiet -r "$DEST/requirements.txt"
echo "Packages installed."

say "3/4 Agent sandbox check (unshare -rn)"
if command -v unshare >/dev/null 2>&1 && unshare -rn true >/dev/null 2>&1; then
  echo "OK: Python missions can run their tests in the sandbox here."
else
  echo "NOT AVAILABLE: Python missions would stop before their checks."
  if [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" = "1" ]; then
    echo "Cause: Ubuntu blocks unprivileged user namespaces. To allow them in this WSL distro, run:"
    echo "  echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/60-hood-userns.conf"
    echo "  sudo sysctl --system"
  else
    echo "Check that 'unshare' exists (package util-linux) and that you are on WSL2, not WSL1:"
    echo "  in PowerShell: wsl -l -v   (VERSION must be 2)"
  fi
  echo "Website missions work either way (they are checked without running code)."
fi

say "4/4 Done"
echo "HOOD is installed in $DEST"
echo "Start it with:  cd $DEST && .venv/bin/python hood_cli.py ui --port $PORT"
echo "Then open http://127.0.0.1:$PORT in Chrome on Windows."
echo "Note: this is a separate HOOD from the Windows one: create the owner account and save your API key"
echo "in Settings again (keys are never copied between installations)."
if [ "$START" = "1" ]; then
  cd "$DEST"
  exec .venv/bin/python hood_cli.py ui --port "$PORT"
fi
