"""In-memory gamepads with the same interface as XInputPads, for tests on Linux."""


class MemoryPads:
    def __init__(self):
        self.pads = {}     # local id -> state tuple
        self.names = {}
        self.rumbles = []  # (local id, strong, weak) received

    def plug(self, local_id, name="Test pad"):
        self.pads[local_id] = (0, 0, 0, 0, 0, 0, 0)
        self.names[local_id] = name

    def unplug(self, local_id):
        self.pads.pop(local_id, None)

    def connected(self):
        return [(i, self.names[i]) for i in sorted(self.pads)]

    def state(self, local_id):
        return self.pads.get(local_id)

    def rumble(self, local_id, strong, weak):
        self.rumbles.append((local_id, strong, weak))
