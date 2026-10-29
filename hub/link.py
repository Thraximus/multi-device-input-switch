"""Raw serial link to the Pico (through the ESP32 DevKit's CP2102), without pyserial."""
import fcntl
import os
import struct
import termios
import time

from . import env, protocol
from .config import SERIAL_PORT

BAUD = env.get_int("SWITCHER_LINK_BAUD", 921600)  # must match the firmware (built with the same .env)


class Link:
    def __init__(self, path=SERIAL_PORT):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        # cfmakeraw equivalent
        attrs[0] = 0  # iflag
        attrs[1] = 0  # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
        attrs[3] = 0  # lflag
        speed = getattr(termios, f"B{BAUD}", None)
        if speed is None:
            raise SystemExit(f"SWITCHER_LINK_BAUD={BAUD} isn't a serial speed this system supports")
        attrs[4] = attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        self.parser = protocol.Parser()

    def reset_esp32(self):
        """Pulse EN through the DevKit's auto-reset circuit (RTS -> EN, DTR -> IO0; DTR stays low so it boots the app)."""
        for flag, bits in ((termios.TIOCMBIC, termios.TIOCM_DTR), (termios.TIOCMBIS, termios.TIOCM_RTS)):
            fcntl.ioctl(self.fd, flag, struct.pack("I", bits))
        time.sleep(0.1)
        fcntl.ioctl(self.fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_RTS))
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        self.parser = protocol.Parser()

    def fileno(self):
        return self.fd

    def send(self, data):
        os.write(self.fd, data)

    def read_frames(self):
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return []
        return self.parser.feed(data)

    def close(self):
        os.close(self.fd)
