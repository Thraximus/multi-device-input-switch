"""Linux evdev key codes -> USB HID usages."""
from evdev import ecodes as e

# Keyboard page (0x07) usages.
KEYBOARD = {getattr(e, f"KEY_{c}"): 0x04 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
KEYBOARD.update({getattr(e, f"KEY_{n}"): 0x1E + i for i, n in enumerate("123456789")})
KEYBOARD.update({getattr(e, f"KEY_F{n}"): 0x3A + n - 1 for n in range(1, 13)})
KEYBOARD.update({getattr(e, f"KEY_F{n}"): 0x68 + n - 13 for n in range(13, 25)})
KEYBOARD.update({getattr(e, f"KEY_KP{n}"): 0x59 + n - 1 for n in range(1, 10)})
KEYBOARD.update({
    e.KEY_0: 0x27, e.KEY_ENTER: 0x28, e.KEY_ESC: 0x29, e.KEY_BACKSPACE: 0x2A, e.KEY_TAB: 0x2B,
    e.KEY_SPACE: 0x2C, e.KEY_MINUS: 0x2D, e.KEY_EQUAL: 0x2E, e.KEY_LEFTBRACE: 0x2F,
    e.KEY_RIGHTBRACE: 0x30, e.KEY_BACKSLASH: 0x31, e.KEY_SEMICOLON: 0x33, e.KEY_APOSTROPHE: 0x34,
    e.KEY_GRAVE: 0x35, e.KEY_COMMA: 0x36, e.KEY_DOT: 0x37, e.KEY_SLASH: 0x38, e.KEY_CAPSLOCK: 0x39,
    e.KEY_SYSRQ: 0x46, e.KEY_SCROLLLOCK: 0x47, e.KEY_PAUSE: 0x48, e.KEY_INSERT: 0x49,
    e.KEY_HOME: 0x4A, e.KEY_PAGEUP: 0x4B, e.KEY_DELETE: 0x4C, e.KEY_END: 0x4D, e.KEY_PAGEDOWN: 0x4E,
    e.KEY_RIGHT: 0x4F, e.KEY_LEFT: 0x50, e.KEY_DOWN: 0x51, e.KEY_UP: 0x52, e.KEY_NUMLOCK: 0x53,
    e.KEY_KPSLASH: 0x54, e.KEY_KPASTERISK: 0x55, e.KEY_KPMINUS: 0x56, e.KEY_KPPLUS: 0x57,
    e.KEY_KPENTER: 0x58, e.KEY_KP0: 0x62, e.KEY_KPDOT: 0x63, e.KEY_102ND: 0x64,
    e.KEY_COMPOSE: 0x65, e.KEY_POWER: 0x66, e.KEY_KPEQUAL: 0x67, e.KEY_KPCOMMA: 0x85,
    e.KEY_RO: 0x87, e.KEY_KATAKANAHIRAGANA: 0x88, e.KEY_YEN: 0x89, e.KEY_HENKAN: 0x8A,
    e.KEY_MUHENKAN: 0x8B, e.KEY_HANGEUL: 0x90, e.KEY_HANJA: 0x91,
})

# Modifier bits of the HID boot keyboard report.
MODIFIERS = {
    e.KEY_LEFTCTRL: 0x01, e.KEY_LEFTSHIFT: 0x02, e.KEY_LEFTALT: 0x04, e.KEY_LEFTMETA: 0x08,
    e.KEY_RIGHTCTRL: 0x10, e.KEY_RIGHTSHIFT: 0x20, e.KEY_RIGHTALT: 0x40, e.KEY_RIGHTMETA: 0x80,
}

# Consumer page (0x0C) usages.
CONSUMER = {
    e.KEY_MUTE: 0xE2, e.KEY_VOLUMEUP: 0xE9, e.KEY_VOLUMEDOWN: 0xEA, e.KEY_PLAYPAUSE: 0xCD,
    e.KEY_NEXTSONG: 0xB5, e.KEY_PREVIOUSSONG: 0xB6, e.KEY_STOPCD: 0xB7, e.KEY_CALC: 0x192,
    e.KEY_MAIL: 0x18A, e.KEY_HOMEPAGE: 0x223, e.KEY_BACK: 0x224, e.KEY_FORWARD: 0x225,
    e.KEY_BRIGHTNESSUP: 0x6F, e.KEY_BRIGHTNESSDOWN: 0x70,
}

# Mouse button bits.
MOUSE_BUTTONS = {e.BTN_LEFT: 0x01, e.BTN_RIGHT: 0x02, e.BTN_MIDDLE: 0x04, e.BTN_SIDE: 0x08, e.BTN_EXTRA: 0x10}
