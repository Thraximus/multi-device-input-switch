"""Type text on a target through its HID keyboard (used to bootstrap the agent).

Keystrokes are layout-dependent: the target interprets HID usages with its own keyboard layout, so
each layout needs its own character table.
"""
import asyncio

from . import protocol

LEFT_SHIFT = 0x02
LEFT_GUI = 0x08
ENTER = 0x28
# Two reports per character (press, release); stay well under the target's 1 kHz polling so the
# Pico's report queue never overflows.
REPORT_INTERVAL = 0.004


def _us():
    table = {c: (0x04 + i, 0) for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}
    table.update({c.upper(): (u, LEFT_SHIFT) for c, (u, _) in list(table.items())})
    table.update({c: (0x1E + i, 0) for i, c in enumerate("123456789")})
    table["0"] = (0x27, 0)
    table.update({c: (0x1E + i, LEFT_SHIFT) for i, c in enumerate("!@#$%^&*(")})
    table[")"] = (0x27, LEFT_SHIFT)
    for plain, shifted, usage in [("-", "_", 0x2D), ("=", "+", 0x2E), ("[", "{", 0x2F), ("]", "}", 0x30),
                                  ("\\", "|", 0x31), (";", ":", 0x33), ("'", '"', 0x34), ("`", "~", 0x35),
                                  (",", "<", 0x36), (".", ">", 0x37), ("/", "?", 0x38)]:
        table[plain] = (usage, 0)
        table[shifted] = (usage, LEFT_SHIFT)
    table[" "] = (0x2C, 0)
    table["\t"] = (0x2B, 0)
    table["\n"] = (ENTER, 0)
    return table


def _uk():
    table = _us()
    table.update({
        '"': (0x1F, LEFT_SHIFT),   # Shift+2
        "@": (0x34, LEFT_SHIFT),   # Shift+'
        "#": (0x32, 0),            # key next to Enter (HID "non-US # and ~")
        "~": (0x32, LEFT_SHIFT),
        "\\": (0x64, 0),           # key left of Z (HID "non-US \\ and |")
        "|": (0x64, LEFT_SHIFT),
        "£": (0x20, LEFT_SHIFT),   # Shift+3
        "¬": (0x35, LEFT_SHIFT),
    })
    return table


LAYOUTS = {"us": _us(), "uk": _uk()}


class Typist:
    def __init__(self, target, layout="us"):
        if layout not in LAYOUTS:
            raise ValueError(f"unknown keyboard layout {layout!r} (known: {', '.join(LAYOUTS)})")
        self.target = target
        self.table = LAYOUTS[layout]

    async def _report(self, modifiers, keys):
        self.target.send(protocol.keyboard(modifiers, keys, self.target.channel))
        await asyncio.sleep(REPORT_INTERVAL)

    async def chord(self, modifiers, usage):
        await self._report(modifiers, [usage])
        await self._report(0, [])

    async def type(self, text):
        missing = sorted({c for c in text if c not in self.table})
        if missing:
            raise ValueError(f"can't type {missing!r} with this layout")
        for ch in text:
            usage, mods = self.table[ch]
            await self._report(mods, [usage])
            await self._report(0, [])

    async def run_dialog(self, command):
        """Win+R, type a command, Enter."""
        await self.chord(LEFT_GUI, 0x15)  # Win+R
        await asyncio.sleep(0.8)
        await self.type(command + "\n")
