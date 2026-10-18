#!/usr/bin/env python3
"""Reads the local mouse and sends its movement/buttons to the Windows host over UDP.

Toggle forwarding on/off with Ctrl+Alt+Shift+F12. Starts enabled.
"""
import argparse
import selectors
import socket
import struct

import evdev
from evdev import ecodes as e

BUTTON_BITS = {e.BTN_LEFT: 1, e.BTN_RIGHT: 2, e.BTN_MIDDLE: 4, e.BTN_SIDE: 8, e.BTN_EXTRA: 16}
MODS = {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL}, {e.KEY_LEFTALT, e.KEY_RIGHTALT}, {e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT}

ap = argparse.ArgumentParser()
ap.add_argument("--host", required=True, help="address of the Windows machine running pico-forward.ps1")
ap.add_argument("--port", type=int, default=47100)
ap.add_argument("--mouse", default="/dev/input/by-id/usb-COMPANY_USB_Device-if01-event-mouse")
ap.add_argument("--kbd", default="/dev/input/by-id/usb-Keychron_Keychron_C3_Pro-event-kbd")
args = ap.parse_args()

mouse = evdev.InputDevice(args.mouse)
kbd = evdev.InputDevice(args.kbd)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
dest = (args.host, args.port)

sel = selectors.DefaultSelector()
sel.register(mouse, selectors.EVENT_READ)
sel.register(kbd, selectors.EVENT_READ)

enabled = True
buttons = dx = dy = wheel = 0


def send(btn, x, y, w):
    sock.sendto(struct.pack("<BBhhb", 0xA5, btn, x, y, w), dest)


print(f"Sending {mouse.name} -> {dest[0]}:{dest[1]}  (Ctrl+Alt+Shift+F12 toggles)")
while True:
    for key, _ in sel.select():
        dev = key.fileobj
        for ev in dev.read():
            if dev is kbd:
                if ev.type == e.EV_KEY and ev.code == e.KEY_F12 and ev.value == 1:
                    held = set(kbd.active_keys())
                    if all(held & m for m in MODS):
                        enabled = not enabled
                        if not enabled:
                            send(0, 0, 0, 0)  # release any held buttons on the host
                        print("forwarding", "ON" if enabled else "OFF")
                continue
            if ev.type == e.EV_REL:
                if ev.code == e.REL_X:
                    dx += ev.value
                elif ev.code == e.REL_Y:
                    dy += ev.value
                elif ev.code == e.REL_WHEEL:
                    wheel += ev.value
            elif ev.type == e.EV_KEY and ev.code in BUTTON_BITS:
                bit = BUTTON_BITS[ev.code]
                buttons = buttons | bit if ev.value else buttons & ~bit
            elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
                if enabled:
                    send(buttons, max(-32768, min(32767, dx)), max(-32768, min(32767, dy)), max(-127, min(127, wheel)))
                dx = dy = wheel = 0
