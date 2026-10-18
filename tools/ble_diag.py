#!/usr/bin/env python3
"""Dump the ESP32 router's Bluetooth diagnostics (stop the hub service first)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hub import protocol as p  # noqa: E402
from hub.link import Link  # noqa: E402
from hub.config import SERIAL_PORT  # noqa: E402

fmt = lambda b: ":".join(f"{x:02x}" for x in b)
link = Link(sys.argv[1] if len(sys.argv) > 1 else SERIAL_PORT)  # default: SWITCHER_SERIAL_PORT
time.sleep(1.5)
link.read_frames()
got = []
for _ in range(5):
    link.send(p.ping(p.CHANNEL_BLE))
    link.send(p.frame(p.MSG_DEBUG_REQ | p.CHANNEL_BLE))
    time.sleep(0.2)
    got += link.read_frames()
d = [x for t, x in got if t == (p.MSG_DEBUG | p.CHANNEL_BLE)]
hosts = [x for t, x in got if t == (p.MSG_BLE_HOSTS | p.CHANNEL_BLE)]
if not d:
    sys.exit("no reply from the ESP32")
d = d[-1]
print("service tables (DIS, battery, HID): create", list(d[0:3]), "handles", list(d[3:6]), "start", list(d[6:9]))
print(f"pairing: attempts {d[9]}, succeeded {d[10]}, last ok {bool(d[11])}, last fail reason 0x{d[12]:02x}, auth mode 0x{d[13]:02x}")
for i in range(14, len(d), 14):
    print("bond:", fmt(d[i:i + 6]), "identity", fmt(d[i + 6:i + 12]), "id key" if d[i + 13] else "no id key")
if hosts:
    print("connected:", [fmt(hosts[-1][i:i + 6]) for i in range(0, len(hosts[-1]), 8)])
