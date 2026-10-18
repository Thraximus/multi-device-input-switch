"""Settings that depend on the machine or are secret, read from the environment.

The repo's `.env` (gitignored; copy `.env.example`) is loaded first; variables already set in the real
environment win. Every Python entry point imports this through hub.config, and the shell tools source the
same file through tools/env.sh, so one file configures everything.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("SWITCHER_ENV_FILE", ROOT / ".env"))


def load(path=ENV_FILE):
    """KEY=VALUE lines; '#' comments; optional quotes; existing environment variables are kept."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def get(name, default=""):
    value = os.environ.get(name, "")
    return value if value != "" else default


def get_int(name, default):
    try:
        return int(get(name, default))
    except ValueError:
        raise SystemExit(f"{name} must be a number, not {get(name)!r}")


def usb_id(name="SWITCHER_USB_ID", default="1209:0001"):
    """'VID:PID' (hex) -> ('1209', '0001') lower-case strings."""
    vid, _, pid = get(name, default).lower().partition(":")
    if len(vid) != 4 or len(pid) != 4:
        raise SystemExit(f"{name} must look like 1209:0001, not {get(name)!r}")
    return vid, pid


load()
