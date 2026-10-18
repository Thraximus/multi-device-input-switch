"""Decides where input goes: switching between Linux and targets, grabbing, the hotkey, LED sync."""
import logging

from evdev import EvdevError
from evdev import ecodes as e

from . import env

log = logging.getLogger(__name__)

MODIFIERS = {"ctrl": {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL}, "shift": {e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT},
             "alt": {e.KEY_LEFTALT, e.KEY_RIGHTALT}, "meta": {e.KEY_LEFTMETA, e.KEY_RIGHTMETA}}


def parse_hotkey(text):
    """'ctrl+scrolllock' -> ([{left, right ctrl codes}], KEY_SCROLLLOCK): modifiers held, then the key."""
    *mods, key = [part.strip().lower() for part in text.split("+")]
    code = getattr(e, f"KEY_{key.upper()}", None)
    if code is None or any(m not in MODIFIERS for m in mods):
        raise SystemExit(f"SWITCHER_HOTKEY={text!r}: use modifiers {sorted(MODIFIERS)} and an evdev key name, "
                         "e.g. ctrl+scrolllock")
    return [MODIFIERS[m] for m in mods], code


# The switch hotkey (cycles Linux → targets → Linux), e.g. ctrl+scrolllock.
HOTKEY_MODIFIERS, HOTKEY = parse_hotkey(env.get("SWITCHER_HOTKEY", "ctrl+scrolllock"))
# HID LED bits -> evdev LED codes
LED_BITS = {0x01: e.LED_NUML, 0x02: e.LED_CAPSL, 0x04: e.LED_SCROLLL}


class Router:
    def __init__(self, pool_getter, targets, on_change):
        self.pool = pool_getter  # callable -> DevicePool (created after the router)
        self.targets = targets   # id -> target
        self.on_change = on_change
        self.active = None       # target id holding the devices, None = Linux
        self.pending = None      # target id we will switch to once every key is released
        self.held = set()        # (device id, code) physically held right now
        self.suppressed = set()  # (device id, code) of hotkey presses not to forward
        self._local_leds = {}    # node path -> set of LED codes lit before grabbing
        self._led_warned = set()

    # --- switching -----------------------------------------------------------

    def switch(self, target_id):
        if target_id is not None and target_id not in self.targets:
            raise ValueError(f"unknown target {target_id}")
        if target_id == self.active and self.pending is None:
            return
        self._leave_active()
        if target_id is None:
            self.pending = None
            log.info("devices -> Linux")
            self.on_change()
            return
        # Grabbing with a key down would leave it stuck on Linux, so wait until everything is released.
        self.pending = target_id
        if not self.held:
            self._complete_switch()
        else:
            self.on_change()

    def cycle(self):
        order = [None] + [t.id for t in self.targets.values() if t.connected]
        current = self.pending if self.pending is not None else self.active
        idx = order.index(current) if current in order else 0
        self.switch(order[(idx + 1) % len(order)])

    def _leave_active(self):
        if self.active is None:
            return
        target = self.targets[self.active]
        self.active = None
        target.hid.release_all()  # sent before deselecting, so it reaches the host we leave
        if getattr(target, "pad", None):
            target.pad.reset()
        target.deactivate()
        for dev in self.pool().devices.values():
            if dev.grabbed:
                dev.ungrab()
                self._restore_local_leds(dev)
                if "gamepad" in dev.kinds:
                    dev.rumble(0, 0)

    def _complete_switch(self):
        target = self.targets[self.pending]
        if not target.connected:
            log.warning("target %s not connected; staying on Linux", target.id)
            self.pending = None
            self.on_change()
            return
        target.activate()
        for dev in self.pool().devices.values():
            if dev.transfer and target.accepts(dev.kinds):
                self._save_local_leds(dev)
                dev.grab()
        self.active, self.pending = target.id, None
        self.on_target_leds(target)
        log.info("devices -> %s", target.name)
        self.on_change()

    # --- events from the pool and targets -------------------------------------

    def on_event(self, dev, ev):
        if ev.type == e.EV_KEY:
            key = (dev.id, ev.code)
            if ev.value == 1:
                self.held.add(key)
            elif ev.value == 0:
                self.held.discard(key)

            held = {code for _, code in self.held}
            if ev.code == HOTKEY and ev.value == 1 and all(held & group for group in HOTKEY_MODIFIERS):
                self.suppressed.add(key)
                self.cycle()
                return
            suppressed = key in self.suppressed
            if suppressed and ev.value == 0:
                self.suppressed.discard(key)
            if self.pending is not None and not self.held:
                self._complete_switch()
                return
            if suppressed:
                return

        if self.active is None or not dev.grabbed:
            return
        target = self.targets[self.active]
        if "gamepad" in dev.kinds and getattr(target, "pad", None):
            target.pad.event(ev)
            return
        hid = target.hid
        if ev.type == e.EV_KEY:
            hid.key(dev.id, ev.code, ev.value)
        elif ev.type == e.EV_REL:
            hid.rel(dev.id, ev.code, ev.value)
        elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
            hid.syn(dev.id)

    def on_transfer_changed(self, dev):
        if self.active is None:
            return
        target = self.targets[self.active]
        if dev.transfer and not dev.grabbed and target.accepts(dev.kinds):
            self._save_local_leds(dev)
            dev.grab()
            self._apply_leds(dev, self.targets[self.active].leds)
        elif dev.grabbed and (not dev.transfer or not target.accepts(dev.kinds)):
            if "gamepad" in dev.kinds and getattr(target, "pad", None):
                target.pad.reset()
                target.pad.flush()  # neutral state on the target
                dev.rumble(0, 0)
            else:
                target.hid.release_source(dev.id)
            dev.ungrab()
            self._restore_local_leds(dev)

    def on_node_added(self, dev, node):
        """A new node of a device appeared (e.g. a device was plugged in while on a target)."""
        if self.active is not None and dev.transfer and self.targets[self.active].accepts(dev.kinds):
            try:
                node.grab()
                dev.grabbed = True
            except OSError as exc:
                log.warning("grab %s failed: %s", node.path, exc)

    def on_target_change(self, target):
        # Never leave the devices grabbed with nowhere to go.
        if not target.connected and target.id in (self.active, self.pending):
            log.warning("target %s lost; devices back to Linux", target.id)
            self.switch(None)
        elif target.id == self.active:
            # What the target accepts may have changed (e.g. its Xbox driver just loaded).
            for dev in list(self.pool().devices.values()):
                self.on_transfer_changed(dev)
        self.on_change()

    def on_target_rumble(self, target, strong, weak):
        if target.id != self.active:
            return
        for dev in self.pool().devices.values():
            if dev.grabbed and "gamepad" in dev.kinds:
                dev.rumble(strong, weak)

    def on_target_leds(self, target):
        if target.id != self.active:
            return
        for dev in self.pool().devices.values():
            if dev.grabbed:
                self._apply_leds(dev, target.leds)

    # --- LEDs ----------------------------------------------------------------
    # LED sync is cosmetic: failures (e.g. a node opened read-only) are logged once and never
    # allowed to interrupt switching.

    def _led_nodes(self, dev):
        return [n for n in dev.nodes.values() if e.EV_LED in n.capabilities()]

    def _set_leds(self, node, lit):
        try:
            for code in LED_BITS.values():
                node.set_led(code, 1 if code in lit else 0)
        except (OSError, EvdevError) as exc:
            if node.path not in self._led_warned:
                self._led_warned.add(node.path)
                log.warning("cannot set LEDs on %s: %s", node.path, exc)

    def _save_local_leds(self, dev):
        for node in self._led_nodes(dev):
            try:
                self._local_leds[node.path] = set(node.leds())
            except (OSError, EvdevError):
                pass

    def _restore_local_leds(self, dev):
        for node in self._led_nodes(dev):
            self._set_leds(node, self._local_leds.pop(node.path, set()))

    def _apply_leds(self, dev, bits):
        lit = {code for bit, code in LED_BITS.items() if bits & bit}
        for node in self._led_nodes(dev):
            self._set_leds(node, lit)
