"""Pool of physical input devices, grouped from their evdev nodes and kept live through udev."""
import logging

import evdev
import evdev.ff
import pyudev
from evdev import ecodes as e

from . import env

log = logging.getLogger(__name__)

# Loop prevention: passthrough devices (our Pico, and the virtual devices the hub creates for input
# arriving from targets) must never become pool devices, or input would circle between machines.
PICO_USB_ID = env.usb_id()  # SWITCHER_USB_ID, the VID:PID the Pico firmware was built with
VIRTUAL_NAME_PREFIX = "pi-switcher-hub"
LOCAL_ORIGIN = "linux"

KIND_KEYBOARD = "keyboard"
KIND_MOUSE = "mouse"
KIND_GAMEPAD = "gamepad"
KIND_OTHER = "other"
# Kinds some target can replay (each target says which it accepts).
SUPPORTED_KINDS = {KIND_KEYBOARD, KIND_MOUSE, KIND_GAMEPAD}


class PhysicalDevice:
    def __init__(self, device_id, name, bus, origin=LOCAL_ORIGIN):
        self.id = device_id
        self.name = name
        self.bus = bus
        self.origin = origin  # machine the device is physically plugged into
        self.nodes = {}  # devnode path -> evdev.InputDevice
        self.node_kinds = {}  # devnode path -> set of udev input kinds
        self.transfer = False
        self.grabbed = False

    @property
    def kinds(self):
        flags = set().union(*self.node_kinds.values()) if self.node_kinds else set()
        kinds = set()
        if "keyboard" in flags:
            kinds.add(KIND_KEYBOARD)
        if "mouse" in flags:
            kinds.add(KIND_MOUSE)
        # Keyboards often expose a "System Control" node that udev also tags as a joystick.
        if "joystick" in flags and KIND_KEYBOARD not in kinds:
            kinds.add(KIND_GAMEPAD)
        return kinds or {KIND_OTHER}

    @property
    def supported(self):
        return bool(self.kinds & SUPPORTED_KINDS)

    def grab(self):
        for dev in self.nodes.values():
            try:
                dev.grab()
            except OSError as exc:
                log.warning("grab %s failed: %s", dev.path, exc)
        self.grabbed = True

    def ungrab(self):
        for dev in self.nodes.values():
            try:
                dev.ungrab()
            except OSError:
                pass
        self.grabbed = False

    def rumble(self, strong, weak):
        """Force feedback on the real device (0-65535 per motor); used for rumble coming back from a target."""
        node = next((n for n in self.nodes.values() if e.EV_FF in n.capabilities()), None)
        if node is None:
            return
        try:
            if not hasattr(self, "_ff_effect"):
                self._ff_effect = evdev.ff.Effect(e.FF_RUMBLE, -1, 0, evdev.ff.Trigger(0, 0), evdev.ff.Replay(0, 0),
                                                  evdev.ff.EffectType(ff_rumble_effect=evdev.ff.Rumble(0, 0)))
            self._ff_effect.u.ff_rumble_effect.strong_magnitude = strong
            self._ff_effect.u.ff_rumble_effect.weak_magnitude = weak
            self._ff_effect.id = node.upload_effect(self._ff_effect)  # first upload assigns the id; later ones update
            node.write(e.EV_FF, self._ff_effect.id, 1 if strong or weak else 0)
        except (OSError, evdev.EvdevError) as exc:
            log.warning("rumble on %s failed: %s", self.name, exc)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "bus": self.bus,
            "origin": self.origin,
            "kinds": sorted(self.kinds),
            "supported": self.supported,
            "transfer": self.transfer,
            "grabbed": self.grabbed,
        }


def _node_kinds(udev_dev):
    props = udev_dev.properties
    return {k for k, prop in (("keyboard", "ID_INPUT_KEYBOARD"), ("mouse", "ID_INPUT_MOUSE"),
                              ("joystick", "ID_INPUT_JOYSTICK")) if props.get(prop) == "1"}


def _passthrough(udev_dev):
    """If this node belongs to a passthrough device, return a description of it, else None."""
    usb = udev_dev.find_parent("usb", "usb_device")
    if usb is not None and (usb.properties.get("ID_VENDOR_ID"), usb.properties.get("ID_MODEL_ID")) == PICO_USB_ID:
        return {"kind": "pico", "serial": usb.properties.get("ID_SERIAL_SHORT", "")}
    parent = udev_dev.parent
    name = parent.properties.get("NAME", "").strip('"') if parent is not None else ""
    if name.startswith(VIRTUAL_NAME_PREFIX):
        return {"kind": "virtual", "name": name}
    return None


def _identify(udev_dev):
    """Return (device_id, name, bus) for an event node, or None if it should be ignored."""
    props = udev_dev.properties
    bus = props.get("ID_BUS")
    if bus not in ("usb", "bluetooth"):
        return None  # built-in buttons, audio jacks, PC speaker, ...
    usb = udev_dev.find_parent("usb", "usb_device")
    if usb is not None:
        up = usb.properties
        serial = up.get("ID_SERIAL") or f"{up.get('ID_VENDOR_ID')}:{up.get('ID_MODEL_ID')}"
        name = f"{up.get('ID_VENDOR_FROM_DATABASE') or up.get('ID_VENDOR', '')} {up.get('ID_MODEL', '')}".strip()
        return f"usb:{serial}", name.replace("_", " "), bus
    # Bluetooth: group by the parent HID device's unique address.
    parent = udev_dev.parent
    uniq = parent.properties.get("UNIQ", "").strip('"') if parent is not None else ""
    name = parent.properties.get("NAME", "").strip('"') if parent is not None else udev_dev.sys_name
    return f"bt:{uniq or name}", name, bus


class DevicePool:
    """Tracks physical devices and calls back on events and on pool changes.

    on_event(device, evdev_event) is called for every event from every node (grabbed or not), so the
    hotkey works no matter which machine currently holds the devices.
    """

    def __init__(self, loop, config, on_event, on_change, on_node_added=None):
        self.loop = loop
        self.config = config
        self.on_event = on_event
        self.on_change = on_change
        self.on_node_added = on_node_added
        self.devices = {}  # id -> PhysicalDevice
        self.passthrough = {}  # devnode -> description; shown in the state, never forwarded
        self._node_owner = {}  # devnode -> PhysicalDevice
        self._context = pyudev.Context()
        self._monitor = None

    def start(self):
        for udev_dev in self._context.list_devices(subsystem="input"):
            self._add_node(udev_dev)
        self._monitor = pyudev.Monitor.from_netlink(self._context)
        self._monitor.filter_by("input")
        self._monitor.start()
        self.loop.add_reader(self._monitor.fileno(), self._on_udev)
        self.on_change()

    def stop(self):
        if self._monitor is not None:
            self.loop.remove_reader(self._monitor.fileno())
        for path in list(self._node_owner):
            self._remove_node(path)

    def get(self, device_id):
        return self.devices.get(device_id)

    def set_transfer(self, device_id, enabled):
        dev = self.devices.get(device_id)
        if dev is None or (enabled and not dev.supported):
            return False
        dev.transfer = enabled
        self.config.set_device_transfer(device_id, enabled)
        return True

    # --- udev ----------------------------------------------------------------

    def _on_udev(self):
        changed = False
        for udev_dev in iter(lambda: self._monitor.poll(timeout=0), None):
            if udev_dev.action == "add":
                changed |= self._add_node(udev_dev)
            elif udev_dev.action == "remove" and udev_dev.device_node:
                changed |= self._remove_node(udev_dev.device_node)
        if changed:
            self.on_change()

    def _add_node(self, udev_dev):
        path = udev_dev.device_node
        if not path or not path.startswith("/dev/input/event") or path in self._node_owner:
            return False
        passthrough = _passthrough(udev_dev)
        if passthrough is not None:
            if path not in self.passthrough:
                self.passthrough[path] = passthrough
                log.info("passthrough device %s ignored: %s", path, passthrough)
                return True
            return False
        ident = _identify(udev_dev)
        if ident is None:
            return False
        device_id, name, bus = ident
        try:
            node = evdev.InputDevice(path)
        except OSError as exc:  # permissions, or already gone again
            log.warning("cannot open %s: %s", path, exc)
            return False

        dev = self.devices.get(device_id)
        if dev is None:
            dev = PhysicalDevice(device_id, name, bus)
            self.devices[device_id] = dev
        dev.nodes[path] = node
        dev.node_kinds[path] = _node_kinds(udev_dev)
        # Keyboards, mice and gamepads follow the switch unless the user turned that off.
        dev.transfer = dev.supported and self.config.device_transfer(device_id, True)
        self._node_owner[path] = dev
        self.loop.add_reader(node.fd, self._on_readable, dev, node)
        log.info("node %s -> %s (%s)", path, dev.name, ",".join(sorted(dev.kinds)))
        if self.on_node_added:
            self.on_node_added(dev, node)
        return True

    def _remove_node(self, path):
        if self.passthrough.pop(path, None) is not None:
            return True
        dev = self._node_owner.pop(path, None)
        if dev is None:
            return False
        node = dev.nodes.pop(path)
        dev.node_kinds.pop(path, None)
        try:
            self.loop.remove_reader(node.fd)
            node.close()
        except (OSError, ValueError):
            pass
        if not dev.nodes:
            del self.devices[dev.id]
            log.info("device removed: %s", dev.name)
        return True

    def _on_readable(self, dev, node):
        try:
            events = list(node.read())
        except BlockingIOError:
            return
        except OSError:  # unplugged; udev will tell us shortly
            self.loop.remove_reader(node.fd)
            return
        for ev in events:
            self.on_event(dev, ev)
