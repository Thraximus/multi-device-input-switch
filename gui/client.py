"""Talks to the hub service over its Unix socket from a background asyncio thread.

One long-lived connection subscribes to state pushes; each request uses its own short connection
(like hub.ctl), so a slow request (flashing the Pico waits for the laptop) never holds up the others.
Results come back to the Qt main thread through signals.
"""
import asyncio
import itertools
import json
import threading

from PySide6.QtCore import QObject, Signal
from websockets.asyncio.client import unix_connect

from hub.config import SOCKET_PATH

RECONNECT_S = 2.0


class HubClient(QObject):
    state_changed = Signal(dict)
    connection_changed = Signal(bool)
    _replied = Signal(int, dict)

    def __init__(self):
        super().__init__()
        self.connected = False
        self.state = None
        self._callbacks = {}
        self._ids = itertools.count(1)
        self._replied.connect(self._deliver)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="hub-client", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        async def shutdown():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._loop.stop()
        if self._thread.is_alive():
            asyncio.run_coroutine_threadsafe(shutdown(), self._loop)
            self._thread.join(2)

    def request(self, msg, callback=None):
        """Send one request; callback(reply) runs on the Qt thread."""
        rid = next(self._ids)
        if callback is not None:
            self._callbacks[rid] = callback
        asyncio.run_coroutine_threadsafe(self._request(rid, msg), self._loop)

    def _deliver(self, rid, reply):
        callback = self._callbacks.pop(rid, None)
        if callback is not None:
            callback(reply)

    # --- background thread ---------------------------------------------------

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._subscribe())
        self._loop.run_forever()

    def _set_connected(self, up):
        if up != self.connected:
            self.connected = up
            self.connection_changed.emit(up)

    async def _subscribe(self):
        while True:
            try:
                async with unix_connect(str(SOCKET_PATH)) as ws:
                    await ws.send(json.dumps({"type": "subscribe"}))
                    self._set_connected(True)
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "state":
                            self.state = msg
                            self.state_changed.emit(msg)
            except asyncio.CancelledError:
                raise
            except Exception:  # service down or restarting
                pass
            self._set_connected(False)
            await asyncio.sleep(RECONNECT_S)

    async def _request(self, rid, msg):
        try:
            async with unix_connect(str(SOCKET_PATH), max_size=None) as ws:
                await ws.send(json.dumps(msg))
                reply = json.loads(await ws.recv())
        except Exception as exc:
            reply = {"type": "error", "error": f"hub service not reachable ({exc})"}
        self._replied.emit(rid, reply)
