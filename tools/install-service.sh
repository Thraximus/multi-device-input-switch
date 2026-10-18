#!/usr/bin/env bash
# Install and start the hub as a systemd user service.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
python=$(command -v python3)
sed -e "s|@REPO@|$repo|" -e "s|@PYTHON@|$python|" "$repo/contrib/pi-switcher-hub.service" > "$unit_dir/pi-switcher-hub.service"
systemctl --user daemon-reload
systemctl --user enable --now pi-switcher-hub.service
systemctl --user --no-pager status pi-switcher-hub.service | head -5
