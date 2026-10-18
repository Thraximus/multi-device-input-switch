"""Xbox-compatible controllers through XInput (ctypes): state and rumble.

Uses XInputGetStateEx (xinput1_4 ordinal 100) when available because it also reports the Guide button;
falls back to the documented XInputGetState.
"""
import ctypes
from ctypes import wintypes

GUIDE = 0x0400
# Our Pico's USB identity: its XInput interface must never be forwarded back (loop prevention). The hub
# sends the real one (SWITCHER_USB_ID) on connect; this is only the firmware's default until then.
DEFAULT_PICO_USB_ID = (0x1209, 0x0001)


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


class XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [("wLeftMotorSpeed", wintypes.WORD), ("wRightMotorSpeed", wintypes.WORD)]


class XINPUT_CAPABILITIES_EX(ctypes.Structure):
    _fields_ = [("Type", ctypes.c_ubyte), ("SubType", ctypes.c_ubyte), ("Flags", wintypes.WORD),
                ("Gamepad", XINPUT_GAMEPAD), ("Vibration", XINPUT_VIBRATION),
                ("VendorId", wintypes.WORD), ("ProductId", wintypes.WORD), ("ProductVersion", wintypes.WORD),
                ("unk1", wintypes.WORD), ("unk2", wintypes.DWORD)]


def _load():
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    raise OSError("XInput not available")


class XInputPads:
    def __init__(self):
        self.pico_usb_id = DEFAULT_PICO_USB_ID
        self.dll = _load()
        try:
            self._get = self.dll[100]  # XInputGetStateEx: includes the Guide button
        except (AttributeError, OSError):
            self._get = self.dll.XInputGetState
        self._get.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_STATE)]
        self._get.restype = wintypes.DWORD
        self.dll.XInputSetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_VIBRATION)]
        self.dll.XInputSetState.restype = wintypes.DWORD
        try:
            self._caps_ex = self.dll[108]  # XInputGetCapabilitiesEx: reports the USB VID/PID
            self._caps_ex.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.POINTER(XINPUT_CAPABILITIES_EX)]
            self._caps_ex.restype = wintypes.DWORD
        except (AttributeError, OSError):
            self._caps_ex = None

    def usb_id(self, slot):
        """(vendor, product) of the controller in a slot, or None if unknown."""
        if self._caps_ex is None:
            return None
        caps = XINPUT_CAPABILITIES_EX()
        if self._caps_ex(1, slot, 0, ctypes.byref(caps)) != 0:
            return None
        return caps.VendorId, caps.ProductId

    def connected(self):
        """[(local id, name)] of connected controllers (XInput slots 0-3)."""
        out = []
        state = XINPUT_STATE()
        for slot in range(4):
            if self._get(slot, ctypes.byref(state)) != 0:
                continue
            usb = self.usb_id(slot)
            if usb == self.pico_usb_id:
                continue  # the Pico's own Xbox interface: input from the hub, not a local device
            name = "Xbox 360 controller" + (f" {usb[0]:04x}:{usb[1]:04x}" if usb else "") + f" (slot {slot + 1})"
            out.append((f"xinput{slot}", name))
        return out

    def state(self, local_id):
        """(buttons, lt, rt, lx, ly, rx, ry), or None if disconnected."""
        state = XINPUT_STATE()
        if self._get(int(local_id[-1]), ctypes.byref(state)) != 0:
            return None
        g = state.Gamepad
        return (g.wButtons, g.bLeftTrigger, g.bRightTrigger, g.sThumbLX, g.sThumbLY, g.sThumbRX, g.sThumbRY)

    def rumble(self, local_id, strong, weak):
        vib = XINPUT_VIBRATION(min(int(strong), 65535), min(int(weak), 65535))
        self.dll.XInputSetState(int(local_id[-1]), ctypes.byref(vib))
