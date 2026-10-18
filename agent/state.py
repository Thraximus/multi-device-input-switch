"""Where the agent keeps its identity and what it learned during pairing.

Kept outside the install folder, so updates and reinstalls don't unpair the agent.
"""
import json
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent  # install root: python/, agent/, VERSION


def state_dir():
    if os.environ.get("PI_SWITCHER_AGENT_HOME"):
        base = Path(os.environ["PI_SWITCHER_AGENT_HOME"])
    elif sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"]) / "pi-switcher-hub" / "state"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "pi-switcher-agent"
    base.mkdir(parents=True, exist_ok=True)
    return base


def version():
    path = APP_DIR / "VERSION"
    return path.read_text().strip() if path.exists() else "dev"


class State:
    def __init__(self):
        self.dir = state_dir()
        self.key_path = self.dir / "agent.key"
        self.cert_path = self.dir / "agent.crt"
        self.hub_path = self.dir / "hub.json"
        self.hub = json.loads(self.hub_path.read_text()) if self.hub_path.exists() else None

    @property
    def paired(self):
        return self.hub is not None and self.cert_path.exists()

    def save_hub(self, hub):
        self.hub = hub
        tmp = self.hub_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(hub, indent=2))
        tmp.replace(self.hub_path)
