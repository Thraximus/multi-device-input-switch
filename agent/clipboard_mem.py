"""In-memory clipboard with the same interface as WindowsClipboard, for tests on Linux."""


class MemoryClipboard:
    def __init__(self):
        self._seq = 0
        self._item = None
        self.secret = False       # what the "app" that copied marked (tests set it)
        self.last_secret = False  # whether the item last read by get() was marked secret

    def sequence(self):
        return self._seq

    def get(self):
        self.last_secret = self.secret
        return self._item

    def set(self, mime, data, secret=False):
        self._item = (mime, data)
        self.secret = secret
        self._seq += 1
