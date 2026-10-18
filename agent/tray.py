"""What the tray shows: the menu entries and the icons. Platform-neutral, so it's testable anywhere;
tray_win.py puts it on the Windows notification area.

The hub pushes {"type": "status", "hub", "self", "holder", "pending", "targets": [{id, name, kind, connected}]}.
"""
from pathlib import Path

# icon keys
HERE, LINUX, LAPTOP, PHONE, TABLET, OFFLINE = "here", "linux", "laptop", "phone", "tablet", "offline"


def target_key(t):
    if t["kind"] == "pico":
        return LAPTOP
    name = t["name"].lower()
    return TABLET if ("tab" in name or "pad" in name) else PHONE


def holder_info(status):
    """(icon key, holder display name) for the machine holding the devices."""
    holder = status.get("holder")
    if holder is None:
        return LINUX, status.get("hub") or "Linux PC"
    t = next((t for t in status["targets"] if t["id"] == holder), None)
    if t is None:
        return LINUX, status.get("hub") or "Linux PC"
    return (HERE if holder == status.get("self") else target_key(t)), t["name"]


def tooltip(status, connected):
    if not connected or status is None:
        return "Switcher Hub — hub not connected"
    key, name = holder_info(status)
    tip = f"Switcher Hub — devices on {name}" + (" (this machine)" if key == HERE else "")
    return tip[:127]


def icon_key(status, connected):
    if not connected or status is None:
        return OFFLINE
    return holder_info(status)[0]


def menu(status, connected, version):
    """Menu entries: dicts with label, and optionally target (switch; None = Linux), checked, enabled,
    action ("log" / "quit"), or separator=True."""
    items = []
    if connected and status is not None:
        _, name = holder_info(status)
        items.append({"label": f"Devices on: {name}", "enabled": False})
        items.append({"separator": True})
        holder = status.get("holder")
        items.append({"label": f"{status.get('hub') or 'Linux PC'} (Linux)", "target": None,
                      "checked": holder is None, "enabled": True})
        for t in status["targets"]:
            label = t["name"] + (" (this machine)" if t["id"] == status.get("self") else "")
            if not t["connected"]:
                label += " — not connected"
            items.append({"label": label, "target": t["id"], "checked": holder == t["id"],
                          "enabled": bool(t["connected"])})
        if status.get("pending"):
            items.append({"label": "switching once all keys are released…", "enabled": False})
    else:
        items.append({"label": "Hub not connected", "enabled": False})
    items.append({"separator": True})
    items.append({"label": f"Agent {version}", "enabled": False})
    items.append({"label": "Open log", "action": "log", "enabled": True})
    items.append({"label": "Quit agent", "action": "quit", "enabled": True})
    return items


# --- icons -----------------------------------------------------------------------------------------------

GREEN = (39, 174, 96, 255)
WHITE = (240, 240, 240, 255)
GREY = (140, 140, 140, 255)
RED = (218, 68, 83, 255)


def draw_icon(key, size=64):
    """A white glyph of the machine holding the devices (Windows tray style); a green badge when it's
    this machine, grey with a red bar when the hub isn't reachable."""
    from PIL import Image, ImageDraw
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = GREY if key == OFFLINE else WHITE
    w = max(2, round(5 * s))

    def box(x0, y0, x1, y1, r=4):
        d.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s], radius=r * s, outline=ink, width=w)

    if key in (HERE, LAPTOP, OFFLINE):
        box(10, 12, 54, 42)
        d.rectangle([2 * s, 46 * s, 62 * s, 52 * s], fill=ink)
    elif key == LINUX:
        box(4, 8, 60, 44)
        d.rectangle([28 * s, 44 * s, 36 * s, 52 * s], fill=ink)
        d.rectangle([18 * s, 51 * s, 46 * s, 56 * s], fill=ink)
    elif key == PHONE:
        box(18, 2, 46, 62, 6)
        d.rectangle([28 * s, 52 * s, 36 * s, 55 * s], fill=ink)
    elif key == TABLET:
        box(6, 8, 58, 56, 6)
        d.rectangle([28 * s, 48 * s, 36 * s, 51 * s], fill=ink)
    if key == HERE:
        d.ellipse([38 * s, 32 * s, 62 * s, 56 * s], fill=GREEN, outline=(0, 0, 0, 255), width=max(1, round(2 * s)))
    elif key == OFFLINE:
        d.line([8 * s, 56 * s, 56 * s, 8 * s], fill=RED, width=max(2, round(6 * s)))
    return img


def write_icons(directory):
    """Write one .ico per key (several sizes each); returns {key: path}."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for key in (HERE, LINUX, LAPTOP, PHONE, TABLET, OFFLINE):
        path = directory / f"tray-{key}.ico"
        draw_icon(key, 256).save(path, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64)])
        out[key] = path
    return out
