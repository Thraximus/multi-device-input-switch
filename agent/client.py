"""The agent's connection to the hub: hello, clipboard sync, gamepads, tray status, updates. Reconnects forever."""
import asyncio
import base64
import hashlib
import itertools
import json
import logging
import platform
import socket
import sys
import threading
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from . import update
from .state import version
from .tls import client_context

log = logging.getLogger(__name__)

CLIPBOARD_POLL = 0.25
DEVICE_SCAN = 1.0
PAD_POLL = 0.002  # 500 Hz
MAX_CLIPBOARD = 16 << 20
MAX_MESSAGE = 24 << 20
RECONNECT_MAX = 30


def item_hash(mime, data):
    return hashlib.sha256(mime.encode() + b"\0" + data).hexdigest()


class Agent:
    def __init__(self, state, clipboard, platform_name=None, check_updates=True, pads=None, tray=None,
                 features=None):
        self.state = state
        self.clipboard = clipboard
        self.pads = pads              # gamepad backend (XInputPads / MemoryPads) or None
        self.tray = tray              # status display with set_status/set_connected/close, or None
        self.forwarded = set()        # local pad ids the hub asked for
        self._pad_thread = None
        self.platform = platform_name or ("windows" if platform.system() == "Windows" else platform.system().lower())
        self.check_updates = check_updates
        self.capabilities = (["clipboard"] if clipboard is not None else []) + (["gamepad"] if pads is not None else [])
        if sys.platform == "win32":
            self.capabilities.append("pico-flash")
        if tray is not None:
            self.capabilities.append("tray")
        self.features = features      # FeatureSet (switchable per device from the PC), or None
        if features is not None:
            features.attach(self.send_threadsafe)
        self._ids = itertools.count(1)
        self._pending = {}
        self._last_hash = None   # last clipboard item we sent or applied (echo suppression)
        self._last_seq = None
        self.ws = None
        self.loop = None
        self.connected = asyncio.Event()

    async def run(self):
        self.loop = asyncio.get_running_loop()
        delay = 1
        while True:
            try:
                await self._session()
                delay = 1
            except (OSError, ConnectionClosed, InvalidHandshake, asyncio.TimeoutError) as exc:
                log.warning("hub connection lost: %s", exc)
            self.connected.clear()
            if self.tray is not None:
                self.tray.set_connected(False)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    async def request(self, msg):
        msg = {**msg, "id": next(self._ids)}
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg["id"]] = fut
        try:
            await self.ws.send(json.dumps(msg))
            return await asyncio.wait_for(fut, 30)
        finally:
            self._pending.pop(msg["id"], None)

    async def _session(self):
        hub = self.state.hub
        async with connect(f"wss://{hub['host']}:{hub['api_port']}", ssl=client_context(self.state),
                           max_size=MAX_MESSAGE, open_timeout=10) as ws:
            self.ws = ws
            log.info("connected to hub %s", hub["host"])
            receiver = asyncio.ensure_future(self._receive())
            try:
                hello = {"type": "hello", "name": socket.gethostname(), "platform": self.platform,
                         "version": version(), "capabilities": self.all_capabilities()}
                if self.features is not None:
                    hello["features"] = self.features.describe()
                welcome = await self.request(hello)
                if self.pads is not None and welcome.get("pico_usb_id"):
                    vid, _, pid = welcome["pico_usb_id"].partition(":")
                    self.pads.pico_usb_id = (int(vid, 16), int(pid, 16))
                self.connected.set()
                if self.tray is not None:
                    self.tray.set_connected(True)
                if self.check_updates:
                    staged = await update.check(self.request, self.state, self.platform)
                    if staged:
                        if self.tray is not None:
                            self.tray.close()
                        update.apply_and_restart(staged)
                watchers = [asyncio.ensure_future(self._watch_clipboard())] if self.clipboard else []
                if self.pads is not None:
                    watchers.append(asyncio.ensure_future(self._watch_devices()))
                    self._start_pad_thread()
                await asyncio.wait([receiver, *watchers], return_when=asyncio.FIRST_COMPLETED)
                for task in watchers:
                    task.cancel()
                receiver.result()  # re-raise why the connection ended
            finally:
                receiver.cancel()
                self._stop_forwarding()
                self.ws = None

    async def _receive(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            fut = self._pending.get(msg.get("id"))
            if fut is not None and not fut.done():
                fut.set_result(msg)
            elif msg.get("type") == "status" and self.tray is not None:
                self.tray.set_status(msg)
            elif msg.get("type") == "config" and self.features is not None:
                if self.features.apply(msg.get("features") or {}):
                    log.info("features now %s", self.features.capabilities() or "none")
                await self.ws.send(json.dumps({"type": "capabilities", "capabilities": self.all_capabilities()}))
            elif self.features is not None and self.features.route(msg):
                pass
            elif msg.get("type") == "clipboard" and self.clipboard is not None:
                self._apply_clipboard(msg)
            elif msg.get("type") == "forward" and self.pads is not None:
                new = set(msg.get("devices", []))
                for local_id in self.forwarded - new:
                    self.pads.rumble(local_id, 0, 0)
                self.forwarded = new
                log.info("forwarding to hub: %s", sorted(new) or "nothing")
            elif msg.get("type") == "pico_flash" and "pico-flash" in self.capabilities:
                asyncio.ensure_future(self._pico_flash(msg))
            elif msg.get("type") == "pad_rumble" and self.pads is not None and msg.get("dev") in self.forwarded:
                self.pads.rumble(msg["dev"], msg.get("strong", 0), msg.get("weak", 0))

    def send_threadsafe(self, msg):
        """Send to the hub from any thread (e.g. a toast button's callback); dropped while offline."""
        ws, loop = self.ws, self.loop
        if ws is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps(msg)), loop)

    def all_capabilities(self):
        return self.capabilities + (self.features.capabilities() if self.features is not None else [])

    def switch(self, target):
        """Ask the hub to move the devices (target id, or None for the Linux PC); safe from any thread."""
        async def send():
            if self.ws is not None:
                reply = await self.request({"type": "switch", "target": target})
                if reply.get("type") == "error":
                    log.warning("switch refused: %s", reply.get("error"))
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(send(), self.loop)

    def _apply_clipboard(self, msg):
        data = base64.b64decode(msg["data"])
        self._last_hash = item_hash(msg["mime"], data)
        try:
            self.clipboard.set(msg["mime"], data, bool(msg.get("secret")))
            self._last_seq = self.clipboard.sequence()  # don't send our own write back
        except OSError as exc:
            log.warning("could not set clipboard: %s", exc)

    async def _watch_clipboard(self):
        while True:
            await asyncio.sleep(CLIPBOARD_POLL)
            seq = self.clipboard.sequence()
            if seq == self._last_seq:
                continue
            self._last_seq = seq
            try:
                item = self.clipboard.get()
            except OSError:
                self._last_seq = None  # busy: try again next poll
                continue
            if item is None:
                continue
            mime, data = item
            if len(data) > MAX_CLIPBOARD or item_hash(mime, data) == self._last_hash:
                continue
            self._last_hash = item_hash(mime, data)
            out = {"type": "clipboard", "mime": mime, "data": base64.b64encode(data).decode()}
            if getattr(self.clipboard, "last_secret", False):
                out["secret"] = True  # synced, but the hub keeps it out of its history
            await self.ws.send(json.dumps(out))

    async def _pico_flash(self, msg):
        from .pico_flash import flash
        try:
            drive = await asyncio.to_thread(flash, base64.b64decode(msg["data"]))
            reply = {"type": "pico_flash_result", "re": msg.get("id"), "ok": True, "drive": drive}
            log.info("Pico flashed via %s", drive)
        except Exception as exc:
            reply = {"type": "pico_flash_result", "re": msg.get("id"), "ok": False, "error": str(exc)}
            log.warning("Pico flash failed: %s", exc)
        await self.ws.send(json.dumps(reply))

    # --- gamepads ------------------------------------------------------------

    async def _watch_devices(self):
        last = None
        while True:
            current = [{"id": i, "name": n, "kind": "gamepad"} for i, n in self.pads.connected()]
            if current != last:
                last = current
                await self.ws.send(json.dumps({"type": "devices", "devices": current}))
            await asyncio.sleep(DEVICE_SCAN)

    def _start_pad_thread(self):
        """Poll forwarded pads in a thread: asyncio timers on Windows are too coarse for 500 Hz."""
        loop = asyncio.get_running_loop()
        ws = self.ws
        stop = threading.Event()
        self._pad_stop = stop

        def run():
            if sys.platform == "win32":
                import ctypes
                ctypes.WinDLL("winmm").timeBeginPeriod(1)
            last = {}
            while not stop.is_set():
                for local_id in list(self.forwarded):
                    state = self.pads.state(local_id)
                    if state is not None and state != last.get(local_id):
                        last[local_id] = state
                        b, lt, rt, lx, ly, rx, ry = state
                        msg = json.dumps({"type": "pad", "dev": local_id, "b": b, "lt": lt, "rt": rt,
                                          "lx": lx, "ly": ly, "rx": rx, "ry": ry})
                        asyncio.run_coroutine_threadsafe(ws.send(msg), loop)
                for local_id in set(last) - self.forwarded:
                    del last[local_id]  # resend full state when forwarding starts again
                time.sleep(PAD_POLL)

        self._pad_thread = threading.Thread(target=run, name="pads", daemon=True)
        self._pad_thread.start()

    def _stop_forwarding(self):
        if self._pad_thread is not None:
            self._pad_stop.set()
            self._pad_thread.join(1)
            self._pad_thread = None
        if self.pads is not None:
            for local_id in self.forwarded:
                self.pads.rumble(local_id, 0, 0)
        self.forwarded = set()
