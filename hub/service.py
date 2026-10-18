"""The hub service: device pool + targets + router + control API.

Run: python3 -m hub.service   (normally via the systemd user unit in contrib/)
"""
import asyncio
import logging
import signal

from .agent_settings import AgentSettings
from .api import Api
from .clipboard import ClipboardService
from .remote import RemoteInput
from .config import Config
from .devices import DevicePool
from .notifications import DesktopNotifications
from .router import Router
from .security import Identity
from .targets import BleChannel, BleTarget, PicoTarget

log = logging.getLogger("hub")


class Hub:
    def __init__(self, loop):
        self.loop = loop
        self.config = Config()
        self.identity = Identity()
        self.targets = {}
        self.router = Router(lambda: self.pool, self.targets, self.state_changed)
        for cfg in self.config["targets"]:
            self._make_target(cfg)
        # The ESP32 router behind each serial port also provides Bluetooth, even before any host is paired.
        for target in list(self.targets.values()):
            if target.kind == "pico":
                self._ble(target.link.port)
        self.pool = DevicePool(loop, self.config, on_event=self.router.on_event,
                               on_change=self.state_changed, on_node_added=self.router.on_node_added)
        self.api = Api(self)
        self.clipboard = ClipboardService(self)
        self.agent_settings = AgentSettings(self.config)
        self.notifications = DesktopNotifications(self)
        self.remote = RemoteInput(self)
        # Audio routing is parked until the Bluetooth dongle (phone) and the agent's audio stream
        # (laptop) exist; the state already carries where audio should play so GUIs can show it.
        self.audio_output = None  # machine id whose speakers/headphones get everyone's audio
        self._state_scheduled = False

    def _make_target(self, cfg):
        callbacks = {"on_change": self.router.on_target_change, "on_leds": self.router.on_target_leds}
        if cfg["kind"] == "pico":
            target = PicoTarget(self.loop, cfg, on_rumble=self.router.on_target_rumble, **callbacks)
        elif cfg["kind"] == "ble":
            target = BleTarget(self._ble(cfg["port"]), cfg, **callbacks)
        else:
            raise ValueError(f"unknown target kind {cfg['kind']!r}")
        self.targets[target.id] = target
        return target

    def _ble(self, port):
        return BleChannel.get(self.loop, port, self._on_new_ble_host, self.state_changed)

    def _on_new_ble_host(self, ble, address):
        """A bonded host the hub doesn't know yet: give it a target (or an unassigned one from config)."""
        target = next((t for t in ble.hosts if t.address is None), None)
        if target is not None:
            target.address = address
            self.config.update_target(target.id, address=address)
        else:
            suffix = address.replace(":", "")[-4:]
            cfg = {"id": f"bt-{suffix}", "name": f"Bluetooth {suffix}", "kind": "ble",
                   "port": ble.port, "address": address}
            self.config.add_target(cfg)
            target = self._make_target(cfg)
        self.state_changed()
        return target

    def bluetooth_channels(self):
        return list(BleChannel._by_port.values())

    def rename_target(self, target_id, name):
        target = self.targets[target_id]
        target.name = name
        self.config.update_target(target_id, name=name)
        self.state_changed()

    def forget_target(self, target_id):
        target = self.targets[target_id]
        if target.kind != "ble":
            raise ValueError("only Bluetooth targets can be forgotten")
        if target_id in (self.router.active, self.router.pending):
            self.router.switch(None)
        if target.address:
            target.ble.forget(target.address)
        target.ble.remove(target)
        del self.targets[target_id]
        self.config.remove_target(target_id)
        self.state_changed()

    async def start(self):
        await self.api.start()
        self.clipboard.start()
        self.notifications.start()
        for target in self.targets.values():
            target.start()
        self.pool.start()

    async def stop(self):
        self.router.switch(None)
        for target in self.targets.values():
            target.stop()
        self.pool.stop()
        self.remote.close()
        await self.clipboard.stop()
        await self.notifications.stop()
        await self.api.stop()

    def state(self):
        online = self.api.online_agents()
        caps = self.api.agent_capabilities()
        return {
            "type": "state",
            "active": self.router.active,
            "pending": self.router.pending,
            "targets": [t.to_dict() for t in self.targets.values()],
            "bluetooth": [b.to_dict() for b in self.bluetooth_channels()],
            "devices": sorted([d.to_dict() for d in self.pool.devices.values()] +
                              [d.to_dict() for d in self.remote.devices.values()], key=lambda d: (d["origin"], d["name"])),
            # Passthrough devices seen on this machine (e.g. the Pico plugged into Linux by mistake).
            "passthrough": [dict(p) for p in sorted({tuple(sorted(p.items())) for p in self.pool.passthrough.values()})],
            "agents": [{"id": a["id"], "name": a["name"], "platform": a["platform"], "target": a["target"],
                        "online": a["id"] in online, "capabilities": caps.get(a["id"], []),
                        **(self.agent_settings.summary(a["id"]) or {})}
                       for a in self.identity.agents().values()],
            "appearance": {**self.agent_settings.appearance, "devices": self.agent_settings.device_theme()},
            "desktop_notifications": self.config.data.get("desktop_notifications", True),
            "clipboard": {"linux": self.clipboard.linux.available,
                          "current": {"mime": self.clipboard.current.mime, "size": len(self.clipboard.current.data),
                                      "origin": self.clipboard.current.origin} if self.clipboard.current else None,
                          "current_id": self.clipboard.current.id if self.clipboard.current else None,
                          "history": self.clipboard.history_summary()},
            "audio": {"output": self.audio_output, "implemented": False,
                      "sources": [a for a, c in caps.items() if "audio" in c]},
        }

    def state_changed(self, *_):
        self.remote.refresh()  # remote devices follow the keyboard/mouse
        # Coalesce bursts (e.g. a device with seven nodes appearing) into one push.
        if not self._state_scheduled:
            self._state_scheduled = True
            self.loop.call_soon(self._push_state)

    def _push_state(self):
        self._state_scheduled = False
        self.api.broadcast(self.state())


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    loop = asyncio.get_running_loop()
    hub = Hub(loop)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await hub.start()
    log.info("hub running")
    await stop.wait()
    log.info("shutting down")
    await hub.stop()


if __name__ == "__main__":
    asyncio.run(main())
