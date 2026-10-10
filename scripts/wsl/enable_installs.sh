#!/usr/bin/env bash
# Switch on HOOD's tool installs in this Ubuntu (WSL2). Run ONCE, as yourself:
#
#   sudo bash ~/hood/scripts/wsl/enable_installs.sh
#
# What it does (nothing else):
#   - installs scripts/wsl/hood-pkg as /usr/local/sbin/hood-pkg (owned by root, not editable by HOOD);
#   - lets your Linux user run exactly that helper with sudo and no password
#     (/etc/sudoers.d/hood-pkg, checked with visudo before it is used).
# The helper only installs/updates/removes the packages on its allowlist (PHP, MariaDB, Node.js,
# Composer, SQLite) from Ubuntu's signed repositories. HOOD still asks you once per tool in the UI.
# Undo:  sudo rm /usr/local/sbin/hood-pkg /etc/sudoers.d/hood-pkg
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "Run it with sudo:  sudo bash $0" >&2
  exit 1
fi
USER_NAME="${SUDO_USER:-}"
if [ -z "$USER_NAME" ] || [ "$USER_NAME" = "root" ]; then
  echo "Run it with sudo from your normal Linux user (not as root), so HOOD's user is known." >&2
  exit 1
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/hood-pkg"
[ -f "$SRC" ] || { echo "hood-pkg not found next to this script" >&2; exit 1; }

install -o root -g root -m 0755 "$SRC" /usr/local/sbin/hood-pkg
sed -i 's/\r$//' /usr/local/sbin/hood-pkg
RULE="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/hood-pkg\n' "$USER_NAME" > "$RULE"
if ! visudo -cf "$RULE" >/dev/null; then
  rm -f "$RULE"
  echo "The sudoers rule did not validate; nothing changed." >&2
  exit 1
fi
install -o root -g root -m 0440 "$RULE" /etc/sudoers.d/hood-pkg
rm -f "$RULE"
echo "Done. HOOD (user $USER_NAME) can now install the tools you approve in its Settings › Tools page:"
grep '^ALLOWED=' /usr/local/sbin/hood-pkg | sed 's/^ALLOWED=/  allowed packages: /'
echo "Undo any time:  sudo rm /usr/local/sbin/hood-pkg /etc/sudoers.d/hood-pkg"
