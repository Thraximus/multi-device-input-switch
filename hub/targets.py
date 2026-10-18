"""Targets: machines that can receive the devices.

All current targets sit behind one serial link (ESP32 router): the Pico (USB HID, channel 0) and the
ESP32's own Bluetooth LE keyboard/mouse (channel 0x40).
"""
import asyncio
import logging
import time

from evdev import ecodes as e

from . import keymap, protocol
from .link import Link

log = logging.getLogger(__name__)

PING_INTERVAL = 0.1
# The Pico answers every ping; this long without a status frame means the link is gone.
STATUS_TIMEOUT = 1.0
RECONNECT_INTERVAL = 1.0


class HidState:
    """Combined keyboard/mouse/consumer state of every device forwarded to one target.

    State is tracked per source device so one device can be released (e.g. its transfer toggle is
    turned off mid-session) without dropping keys held on another.
    """

    def __init__(self, send, channel=0):
        self.send = send
        self.channel = channel
        self.pressed = []        # (source, usage) of non-modifier keys, in press order
        self.modifiers = {}      # source -> modifier bits
        self.buttons = {}        # source -> mouse button bits
        self.consumer = None     # (source, usage) of the last media key held
        self.motion = {}         # source -> [dx, dy, wheel, pan]
        self._last_keyboard = None
        self._last_buttons = 0
        self._last_consumer = 0

    # --- input ---------------------------------------------------------------

    def key(self, source, code, value):
        if value == 2:  # autorepeat is the target's job
            return
        down = value == 1
        if code in keymap.MOUSE_BUTTONS:
            bit = keymap.MOUSE_BUTTONS[code]
            cur = self.buttons.get(source, 0)
            self.buttons[source] = cur | bit if down else cur & ~bit
            self._flush_mouse(source, force=True)
        elif code in keymap.MODIFIERS:
            bit = keymap.MODIFIERS[code]
            cur = self.modifiers.get(source, 0)
            self.modifiers[source] = cur | bit if down else cur & ~bit
            self._send_keyboard()
        elif code in keymap.KEYBOARD:
            entry = (source, keymap.KEYBOARD[code])
            if down and entry not in self.pressed:
                self.pressed.append(entry)
            elif not down and entry in self.pressed:
                self.pressed.remove(entry)
            self._send_keyboard()
        elif code in keymap.CONSUMER:
            if down:
                self.consumer = (source, keymap.CONSUMER[code])
            elif self.consumer == (source, keymap.CONSUMER[code]):
                self.consumer = None
            self._send_consumer()

    def rel(self, source, code, value):
        m = self.motion.setdefault(source, [0, 0, 0, 0])
        if code == e.REL_X:
            m[0] += value
        elif code == e.REL_Y:
            m[1] += value
        elif code == e.REL_WHEEL:
            m[2] += value
        elif code == e.REL_HWHEEL:
            m[3] += value

    def syn(self, source):
        self._flush_mouse(source)

    def release_source(self, source):
        self.pressed = [p for p in self.pressed if p[0] != source]
        self.modifiers.pop(source, None)
        self.buttons.pop(source, None)
        self.motion.pop(source, None)
        if self.consumer and self.consumer[0] == source:
            self.consumer = None
        self._send_keyboard()
        self._send_consumer()
        self._send_buttons_if_changed()

    def release_all(self):
        self.pressed, self.modifiers, self.buttons, self.motion, self.consumer = [], {}, {}, {}, None
        self._last_keyboard, self._last_buttons, self._last_consumer = None, 0, 0
        self.send(protocol.release_all(self.channel))

    # --- reports -------------------------------------------------------------

    def _combined_buttons(self):
        bits = 0
        for b in self.buttons.values():
            bits |= b
        return bits

    def _send_keyboard(self):
        mods = 0
        for m in self.modifiers.values():
            mods |= m
        keys = []
        for _, usage in self.pressed:
            if usage not in keys:
                keys.append(usage)
        report = (mods, tuple(keys[:6]))
        if report != self._last_keyboard:
            self._last_keyboard = report
            self.send(protocol.keyboard(mods, keys, self.channel))

    def _send_consumer(self):
        usage = self.consumer[1] if self.consumer else 0
        if usage != self._last_consumer:
            self._last_consumer = usage
            self.send(protocol.consumer(usage, self.channel))

    def _send_buttons_if_changed(self):
        if self._combined_buttons() != self._last_buttons:
            self._last_buttons = self._combined_buttons()
            self.send(protocol.mouse(self._last_buttons, 0, 0, channel=self.channel))

    def _flush_mouse(self, source, force=False):
        dx, dy, wheel, pan = self.motion.pop(source, (0, 0, 0, 0))
        buttons = self._combined_buttons()
        if force or dx or dy or wheel or pan or buttons != self._last_buttons:
            clamp = lambda v, lim: max(-lim, min(lim, v))
            self._last_buttons = buttons
            self.send(protocol.mouse(buttons, clamp(dx, 32767), clamp(dy, 32767),
                                     clamp(wheel, 127), clamp(pan, 127), self.channel))


# evdev (xpad) codes -> XInput wButtons bits
PAD_BUTTONS = {
    e.BTN_A: 0x1000, e.BTN_B: 0x2000, e.BTN_X: 0x4000, e.BTN_Y: 0x8000,
    e.BTN_TL: 0x0100, e.BTN_TR: 0x0200, e.BTN_SELECT: 0x0020, e.BTN_START: 0x0010, e.BTN_MODE: 0x0400,
    e.BTN_THUMBL: 0x0040, e.BTN_THUMBR: 0x0080,
    # xpad's dpad_to_buttons mode
    e.BTN_TRIGGER_HAPPY1: 0x0004, e.BTN_TRIGGER_HAPPY2: 0x0008, e.BTN_TRIGGER_HAPPY3: 0x0001, e.BTN_TRIGGER_HAPPY4: 0x0002,
}
DPAD_BITS = 0x000F


class PadState:
    """One gamepad forwarded as XInput state (XInput axis orientation: Y up-positive)."""

    def __init__(self, send, channel=0):
        self.send = send
        self.channel = channel
        self.reset()

    def reset(self):
        self.buttons = self.lt = self.rt = self.lx = self.ly = self.rx = self.ry = 0
        self.hat_x = self.hat_y = 0
        self._sent = None

    def event(self, ev):
        if ev.type == e.EV_KEY and ev.code in PAD_BUTTONS and ev.value != 2:
            bit = PAD_BUTTONS[ev.code]
            self.buttons = self.buttons | bit if ev.value else self.buttons & ~bit
        elif ev.type == e.EV_ABS:
            flip = lambda v: max(-32768, min(32767, -v - 1))
            if ev.code == e.ABS_X:
                self.lx = ev.value
            elif ev.code == e.ABS_Y:
                self.ly = flip(ev.value)
            elif ev.code == e.ABS_RX:
                self.rx = ev.value
            elif ev.code == e.ABS_RY:
                self.ry = flip(ev.value)
            elif ev.code == e.ABS_Z:
                self.lt = max(0, min(255, ev.value))
            elif ev.code == e.ABS_RZ:
                self.rt = max(0, min(255, ev.value))
            elif ev.code == e.ABS_HAT0X:
                self.hat_x = ev.value
            elif ev.code == e.ABS_HAT0Y:
                self.hat_y = ev.value
        elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
            self.flush()

    def flush(self):
        hat = ((0x0001 if self.hat_y < 0 else 0) | (0x0002 if self.hat_y > 0 else 0) |
               (0x0004 if self.hat_x < 0 else 0) | (0x0008 if self.hat_x > 0 else 0))
        state = (self.buttons | hat, self.lt, self.rt, self.lx, self.ly, self.rx, self.ry)
        if state != self._sent:
            self._sent = state
            self.send(protocol.gamepad(*state, channel=self.channel))


class SerialLink:
    """One serial port, shared by every channel behind it. Owns reconnecting and the poll loop.

    A channel handler implements on_frame(type, payload), poll() (called every PING_INTERVAL while
    the link is open) and targets() (for connection-change notifications).
    """

    _by_port = {}

    @classmethod
    def get(cls, loop, port):
        if port not in cls._by_port:
            cls._by_port[port] = cls(loop, port)
        return cls._by_port[port]

    def __init__(self, loop, port):
        self.loop = loop
        self.port = port
        self.link = None
        self.channels = {}  # channel -> handler
        self._task = None

    @property
    def is_open(self):
        return self.link is not None

    def register(self, channel, handler):
        self.channels[channel] = handler

    def start(self):
        if self._task is None:
            self._task = self.loop.create_task(self._run())

    def stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        for channel in self.channels:
            self.send(protocol.release_all(channel))
        self._close()
        SerialLink._by_port.pop(self.port, None)

    def send(self, data):
        if self.link is None:
            return
        try:
            self.link.send(data)
        except BlockingIOError:
            log.warning("%s: serial buffer full, dropped %d bytes", self.port, len(data))
        except OSError as exc:
            log.warning("%s: link write failed: %s", self.port, exc)
            self._close()

    def _targets(self):
        return [t for handler in self.channels.values() for t in handler.targets()]

    async def _run(self):
        was = {}
        while True:
            if self.link is None:
                try:
                    self.link = Link(self.port)
                    self.loop.add_reader(self.link.fileno(), self._on_readable)
                    log.info("link open on %s", self.port)
                except OSError:
                    await asyncio.sleep(RECONNECT_INTERVAL)
            else:
                for handler in list(self.channels.values()):
                    handler.poll()
                await asyncio.sleep(PING_INTERVAL)
            for target in self._targets():
                if target.connected != was.get(id(target), False):
                    was[id(target)] = target.connected
                    target.on_change(target)

    def _on_readable(self):
        try:
            frames = self.link.read_frames()
        except OSError:
            self._close()
            return
        for msg_type, payload in frames:
            handler = self.channels.get(msg_type & protocol.CHANNEL_MASK)
            if handler is not None:
                handler.on_frame(msg_type & ~protocol.CHANNEL_MASK, payload)

    def _close(self):
        if self.link is not None:
            try:
                self.loop.remove_reader(self.link.fileno())
                self.link.close()
            except (OSError, ValueError):
                pass
            self.link = None
            for target in self._targets():
                target.mounted = False
            log.info("link closed on %s", self.port)


class PicoTarget:
    """USB HID through the Pico, wired to the ESP32 router's second UART (channel 0)."""

    kind = "pico"
    channel = protocol.CHANNEL_PICO

    def __init__(self, loop, cfg, on_change, on_leds, on_rumble=None):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.on_change = on_change
        self.on_leds = on_leds
        self.on_rumble = on_rumble
        self.mounted = False
        self.suspended = False
        self.xinput = False  # the host bound its Xbox 360 driver to our XInput interface
        self.leds = 0
        self._last_status = 0.0
        self.link = SerialLink.get(loop, cfg["port"])
        self.link.register(self.channel, self)
        self.hid = HidState(self.send, self.channel)
        self.pad = PadState(self.send, self.channel)

    def accepts(self, kinds):
        """Whether a device of these kinds can be sent here."""
        return bool(kinds & {"keyboard", "mouse"}) or ("gamepad" in kinds and self.xinput)

    # channel handler
    def targets(self):
        return [self]

    def poll(self):
        self.send(protocol.ping(self.channel))

    def on_frame(self, msg_type, payload):
        if msg_type == protocol.MSG_STATUS and len(payload) == 2:
            self._last_status = time.monotonic()
            self.mounted = bool(payload[0] & protocol.STATUS_MOUNTED)
            self.suspended = bool(payload[0] & protocol.STATUS_SUSPENDED)
            xinput = bool(payload[0] & protocol.STATUS_XINPUT)
            if xinput != self.xinput:
                self.xinput = xinput
                log.info("%s: Xbox 360 interface %s", self.id, "in use" if xinput else "not in use")
                self.on_change(self)
            self._set_leds(payload[1])
        elif msg_type == protocol.MSG_KBD_LEDS and len(payload) == 1:
            self._set_leds(payload[0])
        elif msg_type == protocol.MSG_RUMBLE and len(payload) == 2 and self.on_rumble:
            self.on_rumble(self, payload[0] * 257, payload[1] * 257)

    # target
    @property
    def responding(self):
        """The Pico answers over the serial link."""
        return self.link.is_open and time.monotonic() - self._last_status < STATUS_TIMEOUT

    @property
    def connected(self):
        """The Pico answers and the target machine has enumerated it."""
        return self.responding and self.mounted

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "connected": self.connected,
                "link": self.link.is_open, "responding": self.responding, "mounted": self.mounted,
                "suspended": self.suspended, "gamepad": self.xinput}

    def start(self):
        self.link.start()

    def stop(self):
        self.link.stop()

    def send(self, data):
        self.link.send(data)

    def send_raw_frame(self, msg_type, payload=b""):
        self.send(protocol.frame(msg_type | self.channel, payload))

    def activate(self):
        pass

    def deactivate(self):
        pass

    def _set_leds(self, leds):
        if leds != self.leds:
            self.leds = leds
            self.on_leds(self)


def format_address(raw):
    return ":".join(f"{b:02x}" for b in raw)


class BleChannel:
    """The ESP32's own Bluetooth LE HID device (channel 0x40), shared by every paired host.

    Each paired host is a BleTarget; the ESP32 sends input only to the selected host, so switching
    between hosts is instant.
    """

    _by_port = {}

    @classmethod
    def get(cls, loop, port, on_new_host, on_state):
        if port not in cls._by_port:
            cls._by_port[port] = cls(loop, port, on_new_host, on_state)
        return cls._by_port[port]

    def __init__(self, loop, port, on_new_host, on_state):
        self.port = port
        self.on_new_host = on_new_host  # callback(channel, address) -> BleTarget
        self.on_state = on_state        # callback() when pairing/connection info changes
        self.link = SerialLink.get(loop, port)
        self.link.register(protocol.CHANNEL_BLE, self)
        self.hosts = []        # BleTargets
        self.connected = {}    # address -> connection interval (ms)
        self.selected = None   # address receiving input
        self.pairing = False
        self.passkey = None
        self._last_status = 0.0

    @property
    def responding(self):
        return self.link.is_open and time.monotonic() - self._last_status < STATUS_TIMEOUT

    def add(self, target):
        self.hosts.append(target)

    def remove(self, target):
        if target in self.hosts:
            self.hosts.remove(target)

    def send(self, data):
        self.link.send(data)

    def send_frame(self, msg_type, payload=b""):
        self.send(protocol.frame(msg_type | protocol.CHANNEL_BLE, payload))

    def select(self, address):
        self.selected = address
        self._send_select()

    def _send_select(self):
        raw = bytes.fromhex(self.selected.replace(":", "")) if self.selected else bytes(6)
        self.send_frame(protocol.MSG_BLE_SELECT, raw)

    def start_pairing(self, seconds=120):
        self.send_frame(protocol.MSG_BLE_PAIR, bytes([min(seconds, 255)]))

    def forget(self, address=None):
        self.send_frame(protocol.MSG_BLE_FORGET, bytes.fromhex(address.replace(":", "")) if address else b"")

    def to_dict(self):
        return {"port": self.port, "responding": self.responding, "pairing": self.pairing,
                "passkey": self.passkey, "connected": sorted(self.connected)}

    # channel handler
    def targets(self):
        return list(self.hosts)

    def poll(self):
        self.send(protocol.ping(protocol.CHANNEL_BLE))
        self._send_select()  # survives an ESP32 reset

    def on_frame(self, msg_type, payload):
        if msg_type == protocol.MSG_STATUS and len(payload) == 2:
            self._last_status = time.monotonic()
            pairing = bool(payload[0] & protocol.STATUS_PAIRING)
            if pairing != self.pairing:
                self.pairing = pairing
                if not pairing:
                    self.passkey = None
                self._notify()
        elif msg_type == protocol.MSG_BLE_HOSTS and len(payload) % 8 == 0:
            connected = {}
            for i in range(0, len(payload), 8):
                address = format_address(payload[i:i + 6])
                connected[address] = int.from_bytes(payload[i + 6:i + 8], "little") * 1.25
            self._update_hosts(connected)
        elif msg_type == protocol.MSG_KBD_LEDS and len(payload) == 7:
            target = self._host(format_address(payload[:6]))
            if target is not None:
                target.set_leds(payload[6])
        elif msg_type == protocol.MSG_BLE_PASSKEY and len(payload) == 4:
            self.passkey = f"{int.from_bytes(payload, 'little'):06d}"
            log.info("Bluetooth pairing code %s", self.passkey)
            self._notify()

    def _host(self, address):
        return next((t for t in self.hosts if t.address == address), None)

    def _update_hosts(self, connected):
        for address in connected:
            if self._host(address) is None:
                target = self.on_new_host(self, address)
                if target is not None:
                    log.info("Bluetooth host %s -> target %s", address, target.id)
        changed = connected.keys() != self.connected.keys() or any(
            self.connected.get(a) != i for a, i in connected.items())
        self.connected = connected
        for target in self.hosts:
            target.mounted = target.address in connected
        if changed:
            self._notify()

    def _notify(self):
        self.on_state()


class BleTarget:
    """One paired Bluetooth host (phone, tablet, ...) behind a BleChannel."""

    kind = "ble"
    channel = protocol.CHANNEL_BLE

    def __init__(self, ble, cfg, on_change, on_leds):
        self.ble = ble
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.address = cfg.get("address")  # None until a host is assigned (migration)
        self.on_change = on_change
        self.on_leds = on_leds
        self.mounted = False
        self.suspended = False
        self.leds = 0
        self.hid = HidState(self.send, self.channel)
        self.pad = None
        ble.add(self)

    @property
    def responding(self):
        return self.ble.responding

    @property
    def connected(self):
        return self.responding and self.mounted

    def accepts(self, kinds):
        return bool(kinds & {"keyboard", "mouse"})

    @property
    def conn_interval_ms(self):
        return self.ble.connected.get(self.address)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "connected": self.connected,
                "link": self.ble.link.is_open, "responding": self.responding, "mounted": self.mounted,
                "suspended": False, "address": self.address, "conn_interval_ms": self.conn_interval_ms}

    def start(self):
        self.ble.link.start()

    def stop(self):
        self.ble.link.stop()

    def send(self, data):
        self.ble.send(data)

    def activate(self):
        self.ble.select(self.address)

    def deactivate(self):
        if self.ble.selected == self.address:
            self.ble.select(None)

    def set_leds(self, leds):
        if leds != self.leds:
            self.leds = leds
            self.on_leds(self)
