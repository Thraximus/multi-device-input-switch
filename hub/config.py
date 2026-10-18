"""Persistent hub settings: targets and per-device transfer toggles (config.json), plus the machine-specific
settings from the environment / .env (see hub/env.py and .env.example)."""
import json
import os
from pathlib import Path

from . import env

CONFIG_DIR = Path(env.get("SWITCHER_CONFIG_DIR",
                          Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pi-switcher-hub"))
CONFIG_FILE = CONFIG_DIR / "config.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / "pi-switcher-hub.sock"

# The ESP32 router's serial port (USB to the hub). Prefer a /dev/serial/by-id/... path: it doesn't change.
SERIAL_PORT = env.get("SWITCHER_SERIAL_PORT", "/dev/ttyUSB0")
API_PORT = env.get_int("SWITCHER_API_PORT", 47110)          # agents (mutual TLS)
PAIRING_PORT = env.get_int("SWITCHER_PAIRING_PORT", 47111)  # pairing + agent download, while a token is open

DEFAULTS = {
    # The USB target behind the ESP32 (Pico). Bluetooth hosts become targets of their own when they pair.
    "targets": [{"id": "laptop", "name": "Laptop", "kind": "pico", "port": SERIAL_PORT}],
    # device id -> {"transfer": bool}
    "devices": {},
}


class Config:
    def __init__(self, path=CONFIG_FILE):
        self.path = Path(path)
        self.data = json.loads(json.dumps(DEFAULTS))
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))
        # Ports come from the environment only (older config files may still carry them).
        self.data["api_port"] = API_PORT
        self.data["pairing_port"] = PAIRING_PORT

    def __getitem__(self, key):
        return self.data[key]

    def device_transfer(self, device_id, default):
        return self.data["devices"].get(device_id, {}).get("transfer", default)

    def set_device_transfer(self, device_id, enabled):
        self.data["devices"].setdefault(device_id, {})["transfer"] = enabled
        self.save()

    def target(self, target_id):
        return next((t for t in self.data["targets"] if t["id"] == target_id), None)

    def add_target(self, cfg):
        self.data["targets"].append(cfg)
        self.save()

    def update_target(self, target_id, **fields):
        self.target(target_id).update(fields)
        self.save()

    def remove_target(self, target_id):
        self.data["targets"] = [t for t in self.data["targets"] if t["id"] != target_id]
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        saved = {k: v for k, v in self.data.items() if k not in ("api_port", "pairing_port")}  # env-only
        tmp.write_text(json.dumps(saved, indent=2) + "\n")
        tmp.replace(self.path)
