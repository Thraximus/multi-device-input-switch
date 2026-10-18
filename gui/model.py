"""Reading the hub's state for display (same wording as `hub.ctl status`)."""
import socket

LINUX = "linux"


def target_icon(t):
    if t is None:
        return "computer"
    if t["kind"] == "pico":
        return "computer-laptop"
    name = t["name"].lower()
    return "tablet" if "tab" in name or "pad" in name else "smartphone"


def target_status(t):
    """(level, text): level is ok / warn / bad / idle."""
    if t["connected"]:
        text = "connected"
        if t.get("suspended"):
            return "warn", "connected, asleep"
        if t.get("conn_interval_ms"):
            text += f" · {t['conn_interval_ms']:g} ms"
        elif t["kind"] == "pico":
            text += " · USB"
        return "ok", text
    if t.get("responding") and t["kind"] == "ble":
        if t.get("address"):
            return "idle", "out of range or Bluetooth off"
        return "idle", "waiting for its first connection"
    if t.get("responding"):
        return "warn", "Pico answers, but the machine hasn't detected it (data cable? port?)"
    if t.get("responding") is False and t["link"] and t["kind"] == "ble":
        return "bad", "ESP32 not answering"
    if t["link"]:
        return "bad", "serial link open, Pico not answering"
    return "bad", "serial adapter not found"


def card_status(t):
    """(level, headline, detail) for a machine card: a short state plus the details on their own line."""
    level, text = target_status(t)
    if t["connected"]:
        head, _, detail = text.partition(" · ")
        return level, head, detail
    return level, {"idle": "not connected", "warn": "not detected"}.get(level, "offline"), text


def holder(state):
    """Id of the machine using the devices ('linux' or a target id)."""
    return state.get("active") or LINUX


def target_by_id(state, tid):
    return next((t for t in state["targets"] if t["id"] == tid), None)


def holder_name(state):
    t = target_by_id(state, holder(state))
    return t["name"] if t else "This PC"


def agent_for(state, tid):
    """The target's agent, preferring one that's online (older pairings stay listed)."""
    agents = [a for a in state["agents"] if a["target"] == tid]
    return next((a for a in agents if a["online"]), agents[0] if agents else None)


def device_groups(state):
    """[(origin id, title, [devices])], this PC first."""
    groups = {}
    for d in state["devices"]:
        groups.setdefault(d.get("origin", LINUX), []).append(d)
    out = [(LINUX, f"This PC ({socket.gethostname()})", groups.pop(LINUX, []))]
    for origin, devs in groups.items():
        t = target_by_id(state, origin)
        out.append((origin, t["name"] if t else origin, devs))
    return out


def device_icon(d):
    kinds = d["kinds"]
    if "gamepad" in kinds:
        return "input-gaming"
    if "keyboard" in kinds:
        return "input-keyboard"
    if "mouse" in kinds:
        return "input-mouse"
    return "input-tablet"


def device_bus(d):
    """(tag text, icon) for how a device reaches its machine; None if unknown."""
    return {"usb": ("USB", "drive-removable-media-usb"),
            "bluetooth": ("BT", "network-bluetooth"),
            "remote": ("network", "network-wireless")}.get(d.get("bus"))


def target_bus(t):
    """(tag text, icon) for how the hub reaches a machine: the Pico is USB, the ESP32 is Bluetooth."""
    return device_bus({"bus": "usb" if t["kind"] == "pico" else "bluetooth"})


def human_size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
