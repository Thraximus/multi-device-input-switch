"""Two-way forwarding: devices plugged into targets, recreated on Linux.

Agents report their local input devices; those join the device pool with the target as origin. Remote
devices with transfer enabled are forwarded while the keyboard/mouse are on Linux (devices follow the
switch). Gamepads are recreated as a virtual Xbox 360 pad (uinput); rumble goes back to the real pad.

Loop prevention: virtual devices are named "pi-switcher-hub ...", which the device pool ignores.
"""
import logging

from evdev import AbsInfo, UInput
from evdev import ecodes as e

log = logging.getLogger(__name__)

VIRTUAL_PREFIX = "pi-switcher-hub"

# XInput button bits -> evdev codes (xpad layout)
XINPUT_BUTTONS = {
    0x1000: e.BTN_A, 0x2000: e.BTN_B, 0x4000: e.BTN_X, 0x8000: e.BTN_Y,
    0x0100: e.BTN_TL, 0x0200: e.BTN_TR, 0x0020: e.BTN_SELECT, 0x0010: e.BTN_START, 0x0400: e.BTN_MODE,
    0x0040: e.BTN_THUMBL, 0x0080: e.BTN_THUMBR,
}
DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008


class RemoteDevice:
    def __init__(self, agent_id, origin, local_id, name, kind, transfer):
        self.agent_id = agent_id
        self.origin = origin          # target id of the machine it's plugged into
        self.local_id = local_id      # id on the agent
        self.id = f"remote:{agent_id}:{local_id}"
        self.name = name
        self.kind = kind
        self.transfer = transfer
        self.forwarding = False

    def to_dict(self):
        return {"id": self.id, "name": self.name, "bus": "remote", "origin": self.origin, "kinds": [self.kind],
                "supported": self.kind == "gamepad", "transfer": self.transfer, "grabbed": self.forwarding}


class VirtualPad:
    """A uinput Xbox 360 pad fed with XInput states; rumble requests go to on_rumble(strong, weak)."""

    def __init__(self, loop, name, on_rumble):
        stick = AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)
        trigger = AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)
        hat = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)
        caps = {
            e.EV_KEY: sorted(XINPUT_BUTTONS.values()),
            e.EV_ABS: [(e.ABS_X, stick), (e.ABS_Y, stick), (e.ABS_RX, stick), (e.ABS_RY, stick),
                       (e.ABS_Z, trigger), (e.ABS_RZ, trigger), (e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat)],
            e.EV_FF: [e.FF_RUMBLE, e.FF_PERIODIC, e.FF_SQUARE, e.FF_TRIANGLE, e.FF_SINE, e.FF_GAIN],
        }
        # Microsoft's VID/PID so SDL, Steam and games map it as an Xbox 360 controller.
        self.ui = UInput(caps, name=f"{VIRTUAL_PREFIX} {name}", vendor=0x045E, product=0x028E,
                         version=0x0114, bustype=e.BUS_USB, max_effects=16)
        self.loop = loop
        self.on_rumble = on_rumble
        self.effects = {}  # effect id -> (strong, weak)
        self.last = None
        loop.add_reader(self.ui.fd, self._on_ff)

    def update(self, state):
        if state == self.last:
            return
        self.last = state
        buttons, lt, rt, lx, ly, rx, ry = state
        for bit, code in XINPUT_BUTTONS.items():
            self.ui.write(e.EV_KEY, code, 1 if buttons & bit else 0)
        hat_x = (1 if buttons & DPAD_RIGHT else 0) - (1 if buttons & DPAD_LEFT else 0)
        hat_y = (1 if buttons & DPAD_DOWN else 0) - (1 if buttons & DPAD_UP else 0)
        flip = lambda v: max(-32768, min(32767, -v - 1))  # XInput Y is up-positive, evdev down-positive
        for code, value in ((e.ABS_X, lx), (e.ABS_Y, flip(ly)), (e.ABS_RX, rx), (e.ABS_RY, flip(ry)),
                            (e.ABS_Z, lt), (e.ABS_RZ, rt), (e.ABS_HAT0X, hat_x), (e.ABS_HAT0Y, hat_y)):
            self.ui.write(e.EV_ABS, code, value)
        self.ui.syn()

    def release(self):
        self.update((0, 0, 0, 0, 0, 0, 0))

    def close(self):
        try:
            self.loop.remove_reader(self.ui.fd)
            self.release()
            self.ui.close()
        except OSError:
            pass

    def _on_ff(self):
        try:
            events = list(self.ui.read())
        except (BlockingIOError, OSError):
            return
        for ev in events:
            if ev.type == e.EV_UINPUT and ev.code == e.UI_FF_UPLOAD:
                upload = self.ui.begin_upload(ev.value)
                upload.retval = 0
                effect = upload.effect
                if effect.type == e.FF_RUMBLE:
                    rumble = effect.u.ff_rumble_effect
                    self.effects[effect.id] = (rumble.strong_magnitude, rumble.weak_magnitude)
                else:  # periodic effects: approximate with a medium rumble on both motors
                    level = min(65535, abs(effect.u.ff_periodic_effect.magnitude) * 2)
                    self.effects[effect.id] = (level, level)
                self.ui.end_upload(upload)
            elif ev.type == e.EV_UINPUT and ev.code == e.UI_FF_ERASE:
                erase = self.ui.begin_erase(ev.value)
                erase.retval = 0
                self.effects.pop(erase.effect_id, None)
                self.ui.end_erase(erase)
            elif ev.type == e.EV_FF and ev.code in self.effects:
                self.on_rumble(*(self.effects[ev.code] if ev.value else (0, 0)))


class RemoteInput:
    def __init__(self, hub):
        self.hub = hub
        self.devices = {}  # id -> RemoteDevice
        self.pads = {}     # id -> VirtualPad
        self._sent = {}    # agent id -> local ids we last asked it to forward

    # --- from agents ---------------------------------------------------------

    def agent_devices(self, agent, devices):
        """An agent's current device list (sent on connect and whenever it changes)."""
        agent_id = agent["id"]
        seen = set()
        for d in devices:
            kind = d.get("kind")
            dev_id = f"remote:{agent_id}:{d['id']}"
            seen.add(dev_id)
            if dev_id not in self.devices:
                default = kind == "gamepad"
                self.devices[dev_id] = RemoteDevice(agent_id, agent["target"], d["id"], d.get("name", d["id"]),
                                                    kind, self.hub.config.device_transfer(dev_id, default))
                log.info("remote device %s on %s (%s)", d.get("name"), agent["target"], kind)
        for dev_id in [i for i, dev in self.devices.items() if dev.agent_id == agent_id and i not in seen]:
            self._stop(dev_id)
            del self.devices[dev_id]
        self.refresh()

    def agent_gone(self, agent_id):
        for dev_id in [i for i, dev in self.devices.items() if dev.agent_id == agent_id]:
            self._stop(dev_id)
            del self.devices[dev_id]
        self._sent.pop(agent_id, None)

    def pad_state(self, agent_id, msg):
        pad = self.pads.get(f"remote:{agent_id}:{msg['dev']}")
        if pad is not None:
            pad.update((int(msg["b"]), int(msg["lt"]), int(msg["rt"]),
                        int(msg["lx"]), int(msg["ly"]), int(msg["rx"]), int(msg["ry"])))

    # --- decisions -----------------------------------------------------------

    def set_transfer(self, dev_id, enabled):
        dev = self.devices.get(dev_id)
        if dev is None or dev.kind != "gamepad":
            return False
        dev.transfer = enabled
        self.hub.config.set_device_transfer(dev_id, enabled)
        self.refresh()
        return True

    def refresh(self):
        """Forward transfer-enabled remote devices while the keyboard/mouse are on Linux."""
        on_linux = self.hub.router.active is None and self.hub.router.pending is None
        for dev_id, dev in self.devices.items():
            want = on_linux and dev.transfer
            if want and not dev.forwarding:
                self._start(dev)
            elif not want and dev.forwarding:
                self._stop(dev_id)
        wanted = {}
        for dev in self.devices.values():
            if dev.forwarding:
                wanted.setdefault(dev.agent_id, []).append(dev.local_id)
        for agent_id in {d.agent_id for d in self.devices.values()} | set(self._sent):
            ids = sorted(wanted.get(agent_id, []))
            if self._sent.get(agent_id) != ids:
                self._sent[agent_id] = ids
                self.hub.api.send_to_agent(agent_id, {"type": "forward", "devices": ids})

    def _start(self, dev):
        def rumble(strong, weak, dev=dev):
            self.hub.api.send_to_agent(dev.agent_id, {"type": "pad_rumble", "dev": dev.local_id,
                                                      "strong": strong, "weak": weak})
        try:
            self.pads[dev.id] = VirtualPad(self.hub.loop, dev.name, rumble)
        except OSError as exc:
            log.error("cannot create virtual pad (is /dev/uinput writable?): %s", exc)
            return
        dev.forwarding = True
        log.info("forwarding %s from %s", dev.name, dev.origin)

    def _stop(self, dev_id):
        pad = self.pads.pop(dev_id, None)
        if pad is not None:
            pad.close()
        dev = self.devices.get(dev_id)
        if dev is not None and dev.forwarding:
            dev.forwarding = False
            log.info("stopped forwarding %s", dev.name)

    def close(self):
        for dev_id in list(self.pads):
            self._stop(dev_id)
