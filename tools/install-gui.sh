#!/usr/bin/env bash
# Add the tray app to the application menu and start it at login.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$apps"
cat > "$apps/pi-switcher-hub-gui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Switcher Hub
Comment=Send keyboard, mouse and controller to the laptop, phone or tablet
Exec=sh -c 'cd "$repo" && exec $(command -v python3) -m gui'
Icon=input-keyboard
Categories=Utility;Settings;
Keywords=kvm;switch;keyboard;mouse;
DESKTOP
python3 -c "import sys; sys.path.insert(0, '$repo'); from gui.settings import set_autostart; set_autostart(True)"
echo "installed: $apps/pi-switcher-hub-gui.desktop (+ login autostart)"
echo "start now: cd $repo && python3 -m gui"
