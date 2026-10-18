"""Flash the Pico through the machine it's plugged into: after the hub reboots it into its bootloader,
the RP2040 shows up as a USB drive labelled RPI-RP2; copying a .uf2 file onto it flashes it."""
import ctypes
import string
import time
from ctypes import wintypes

LABEL = "RPI-RP2"


def _find_drive():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mask = kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) != 2:  # DRIVE_REMOVABLE
            continue
        name = ctypes.create_unicode_buffer(64)
        if kernel32.GetVolumeInformationW(root, name, 64, None, None, None, None, 0) and name.value == LABEL:
            return root
    return None


def flash(uf2, timeout=20.0):
    """Wait for the bootloader drive and copy the firmware onto it. Returns the drive used."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        drive = _find_drive()
        if drive:
            time.sleep(0.5)  # let Windows finish mounting
            with open(drive + "firmware.uf2", "wb") as f:
                f.write(uf2)
            return drive
        time.sleep(0.25)
    raise TimeoutError(f"no {LABEL} drive appeared")
