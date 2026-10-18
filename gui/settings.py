"""GUI preferences (theme) and the login autostart entry."""
import json
import sys
from pathlib import Path

from hub.config import CONFIG_DIR

GUI_FILE = CONFIG_DIR / "gui.json"
AUTOSTART = Path.home() / ".config/autostart/pi-switcher-hub-gui.desktop"
REPO = Path(__file__).resolve().parent.parent

DEFAULTS = {"theme": "plasma"}


class Settings:
    def __init__(self, path=GUI_FILE):
        self.path = path
        self.data = dict(DEFAULTS)
        try:
            self.data.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")


def autostart_enabled():
    return AUTOSTART.is_file()


def set_autostart(enabled):
    if not enabled:
        AUTOSTART.unlink(missing_ok=True)
        return
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=pi-switcher-hub\n"
        "Comment=Keyboard/mouse switcher tray\n"
        f"Exec=sh -c 'cd \"{REPO}\" && exec {sys.executable} -m gui --hidden'\n"
        "Icon=input-keyboard\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
