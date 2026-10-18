#!/usr/bin/env python3
"""Moves the cursor in a square by writing packets straight to the Pico's serial port."""
import struct, sys, time
port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
with open(port, "wb", buffering=0) as f:
    for dx, dy in [(200, 0), (0, 200), (-200, 0), (0, -200)] * 2:
        f.write(struct.pack("<BBhhb", 0xA5, 0, dx, dy, 0))
        time.sleep(0.4)
print("done")
