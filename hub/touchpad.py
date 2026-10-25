"""Phones and tablets as a touchpad and keyboard for any machine: the app sends pointer deltas and typing, the
hub moves that machine's mouse and presses its keys.

From an agent with the "touchpad" capability (target: a target id, or null for Linux):
- {"type": "pointer", "target", "dx", "dy", "wheel", "pan", "buttons"}: buttons are the full state (bit 0
  left, 1 right, 2 middle), so a lost message can't leave one stuck; "release": true lets go of everything.
- {"type": "keys", "target", "text"}: type text; or {"type": "keys", "target", "key", "mods"}: one key
  ("enter", "left", "f5", or a character) with modifiers held ("ctrl", "alt", "shift", "meta").

Text becomes key presses with the layout the machine uses (SWITCHER_KEYBOARD_LAYOUT); characters that layout
can't type are skipped and reported back as {"type": "touchpad_skipped", "text"}.

Targets: Linux gets a virtual mouse and keyboard (uinput); the laptop gets the Pico's mouse and keyboard
alongside the real devices; a Bluetooth device gets the ESP32's, which reach only the selected host — so a
Bluetooth device can be driven unless the keyboard is on another Bluetooth device right now. The hub answers
the first message for a new target, and every change, with {"type": "touchpad", "target", "ok", "reason"}.
"""
import asyncio
import logging

from evdev import UInput
from evdev import ecodes as e

from . import env, keymap
from .remote import VIRTUAL_PREFIX
from .typing import LAYOUTS, LEFT_SHIFT

log = logging.getLogger(__name__)

BUTTONS = {0x01: e.BTN_LEFT, 0x02: e.BTN_RIGHT, 0x04: e.BTN_MIDDLE}
LIMIT = 2000  # largest step per message, in counts (a runaway value must not fling the pointer)
LAYOUT = env.get("SWITCHER_KEYBOARD_LAYOUT") or env.get("SWITCHER_INSTALL_LAYOUT", "us")
MAX_TEXT = 4000
# Pause between key events: the Pico and the ESP32 queue reports, typing faster than this could overflow them.
KEY_INTERVAL = 0.004

USAGE_TO_CODE = {usage: code for code, usage in keymap.KEYBOARD.items()}
MODIFIER_KEYS = {"ctrl": e.KEY_LEFTCTRL, "shift": e.KEY_LEFTSHIFT, "alt": e.KEY_LEFTALT, "meta": e.KEY_LEFTMETA}
NAMED_KEYS = {
    "enter": e.KEY_ENTER, "backspace": e.KEY_BACKSPACE, "delete": e.KEY_DELETE, "tab": e.KEY_TAB,
    "esc": e.KEY_ESC, "space": e.KEY_SPACE, "up": e.KEY_UP, "down": e.KEY_DOWN, "left": e.KEY_LEFT,
    "right": e.KEY_RIGHT, "home": e.KEY_HOME, "end": e.KEY_END, "pageup": e.KEY_PAGEUP,
    "pagedown": e.KEY_PAGEDOWN, "insert": e.KEY_INSERT, "printscreen": e.KEY_SYSRQ,
    **{f"f{n}": getattr(e, f"KEY_F{n}") for n in range(1, 13)},
}


class VirtualInput:
    """The touchpad and keyboard on Linux: a uinput device, ignored by the device pool through its name."""

    def __init__(self):
        caps = {e.EV_KEY: sorted(set(BUTTONS.values()) | set(keymap.KEYBOARD) | set(keymap.MODIFIERS)),
                e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL]}
        self.ui = UInput(caps, name=f"{VIRTUAL_PREFIX} touchpad", bustype=e.BUS_VIRTUAL)
        self.buttons = 0
        self.held = set()

    def move(self, buttons, dx, dy, wheel, pan):
        for bit, code in BUTTONS.items():
            if (buttons ^ self.buttons) & bit:
                self.ui.write(e.EV_KEY, code, 1 if buttons & bit else 0)
        self.buttons = buttons
        for code, value in ((e.REL_X, dx), (e.REL_Y, dy), (e.REL_WHEEL, wheel), (e.REL_HWHEEL, pan)):
            if value:
                self.ui.write(e.EV_REL, code, value)
        self.ui.syn()

    def key(self, code, down):
        (self.held.add if down else self.held.discard)(code)
        self.ui.write(e.EV_KEY, code, 1 if down else 0)
        self.ui.syn()

    def release(self):
        for code in list(self.held):
            self.key(code, False)
        self.move(0, 0, 0, 0, 0)

    def close(self):
        self.release()
        self.ui.close()


def key_events(text=None, key=None, mods=(), layout=LAYOUT):
    """[(evdev code, down)] for typing text or one key with modifiers, and the characters left out."""
    table = LAYOUTS[layout]
    events, skipped = [], ""

    def tap(code, shift):
        if shift:
            events.append((e.KEY_LEFTSHIFT, True))
        events.extend([(code, True), (code, False)])
        if shift:
            events.append((e.KEY_LEFTSHIFT, False))

    if text is not None:
        for ch in str(text)[:MAX_TEXT].replace("\r\n", "\n"):
            if ch in table:
                usage, modifiers = table[ch]
                tap(USAGE_TO_CODE[usage], bool(modifiers & LEFT_SHIFT))
            else:
                skipped += ch
        return events, skipped
    held = [MODIFIER_KEYS[m] for m in mods if m in MODIFIER_KEYS]
    key = str(key or "")
    if key.lower() in NAMED_KEYS:
        code, shift = NAMED_KEYS[key.lower()], False
    elif len(key) == 1 and key in table:
        usage, modifiers = table[key]
        code, shift = USAGE_TO_CODE[usage], bool(modifiers & LEFT_SHIFT)
        # Ctrl+C means the C key, not Ctrl+Shift+C: a shortcut's letter is typed as lowercase.
        if held and key.isalpha():
            shift = "shift" in mods
    else:
        return [], key
    if shift and e.KEY_LEFTSHIFT not in held:
        held.append(e.KEY_LEFTSHIFT)
    events = [(c, True) for c in held] + [(code, True), (code, False)] + [(c, False) for c in reversed(held)]
    return events, skipped


class Touchpads:
    def __init__(self, hub):
        self.hub = hub
        self.linux = None     # VirtualInput, created on first use
        self.sessions = {}    # agent id -> {"target", "buttons", "ok", "keys": Queue, "typist": Task}

    def _session(self, agent, target_id, reply):
        """The agent's session, moved to target_id (letting go of the old machine); None if it can't be driven."""
        session = self.sessions.setdefault(agent["id"], {"target": None, "buttons": 0, "ok": None,
                                                         "keys": None, "typist": None})
        if target_id != session["target"]:
            self._release(agent["id"])
            session.update(target=target_id, buttons=0, ok=None)
        reason = self._usable(agent, target_id)
        if (reason is None) != session["ok"]:
            session["ok"] = reason is None
            reply({"type": "touchpad", "target": target_id, "ok": reason is None, "reason": reason or ""})
        return session if reason is None else None

    def pointer(self, agent, msg, reply):
        """One pointer message from an agent; reply(dict) answers the agent when its state changes."""
        if msg.get("release"):
            self._release(agent["id"])
            return
        session = self._session(agent, msg.get("target"), reply)
        if session is None:
            return
        clamp = lambda v, lim: max(-lim, min(lim, int(v or 0)))
        dx, dy = clamp(msg.get("dx"), LIMIT), clamp(msg.get("dy"), LIMIT)
        wheel, pan = clamp(msg.get("wheel"), 127), clamp(msg.get("pan"), 127)
        buttons = int(msg.get("buttons") or 0) & 0x07
        if session["target"] is None:
            self._linux().move(buttons, dx, dy, wheel, pan)
        else:
            hid, source = self._hid(session), f"touchpad:{agent['id']}"
            for bit, code in BUTTONS.items():
                if (buttons ^ session["buttons"]) & bit:
                    hid.key(source, code, 1 if buttons & bit else 0)
            for code, value in ((e.REL_X, dx), (e.REL_Y, dy), (e.REL_WHEEL, wheel), (e.REL_HWHEEL, pan)):
                hid.rel(source, code, value)
            hid.syn(source)
        session["buttons"] = buttons

    def keys(self, agent, msg, reply):
        """Typing from an agent: queued, and pressed one key event at a time on the session's machine."""
        session = self._session(agent, msg.get("target"), reply)
        if session is None:
            return
        events, skipped = key_events(msg.get("text"), msg.get("key"), msg.get("mods") or ())
        if skipped:
            reply({"type": "touchpad_skipped", "text": skipped})
        if not events:
            return
        if session["keys"] is None:
            session["keys"] = asyncio.Queue()
            session["typist"] = asyncio.ensure_future(self._type(agent["id"], session))
        for event in events:
            session["keys"].put_nowait((session["target"], event))

    async def _type(self, agent_id, session):
        source = f"touchpad:{agent_id}"
        while True:
            target_id, (code, down) = await session["keys"].get()
            if target_id != session["target"]:
                continue  # queued for a machine the session has left
            if target_id is None:
                self._linux().key(code, down)
            else:
                self._hid(session).key(source, code, 1 if down else 0)
            await asyncio.sleep(KEY_INTERVAL)

    def _linux(self):
        if self.linux is None:
            self.linux = VirtualInput()
        return self.linux

    def _hid(self, session):
        target = self.hub.targets[session["target"]]
        if target.kind == "ble" and target.ble.selected != target.address:
            target.ble.select(target.address)
        return target.hid

    def _usable(self, agent, target_id):
        """None if the touchpad can drive this machine now, else why not."""
        if target_id is None:
            return None
        target = self.hub.targets.get(target_id)
        if target is None:
            return "unknown machine"
        if target_id == agent.get("target"):
            return "that's this device"
        if not target.connected:
            return f"{target.name} isn't connected"
        if target.kind == "ble":
            active = self.hub.targets.get(self.hub.router.active)
            if active is not None and active.kind == "ble" and active.ble is target.ble and active is not target:
                return f"the keyboard is on {active.name} (Bluetooth reaches one device at a time)"
        return None

    def _release(self, agent_id):
        """Let go of every button and key the agent holds, and drop typing still queued."""
        session = self.sessions.get(agent_id)
        if not session:
            return
        if session["keys"] is not None:
            while not session["keys"].empty():
                session["keys"].get_nowait()
        target_id = session["target"]
        if target_id is None:
            if self.linux is not None:
                self.linux.release()
        elif target_id in self.hub.targets:
            self.hub.targets[target_id].hid.release_source(f"touchpad:{agent_id}")
        session["buttons"] = 0

    def agent_gone(self, agent_id):
        self._release(agent_id)
        session = self.sessions.pop(agent_id, None)
        if session and session["typist"] is not None:
            session["typist"].cancel()
        if not self.sessions and self.linux is not None:
            self.linux.close()
            self.linux = None

    def close(self):
        for agent_id in list(self.sessions):
            self.agent_gone(agent_id)
