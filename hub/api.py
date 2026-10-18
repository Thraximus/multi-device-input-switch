"""Control API.

Three listeners, all speaking JSON messages over WebSockets:
- Unix socket (local GUI/CLI), protected by file permissions: full control, including pairing.
- TCP + mutual TLS (paired agents): state, switching, device toggles, updates.
- TCP + TLS pairing port: only open while a pairing token is active; accepts a single "pair" message,
  and serves the agent bootstrap download over HTTPS (GET /agent/<token>/...), token required.

Agents send {"type": "hello", "capabilities": [...]} after connecting; features are only offered to
agents that announce them ("clipboard", "gamepad", "pico-flash", "tray"; "audio" reserved). Agents with
"tray" are pushed {"type": "status"} (holder + targets) and may send "switch".

Requests carry an optional "id" which is echoed in the reply. The hub pushes {"type": "state"} to
every subscribed connection whenever something changes.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import socket
from pathlib import Path

from websockets.asyncio.server import serve, unix_serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from . import env
from .config import CONFIG_DIR, SOCKET_PATH
from .devices import PICO_USB_ID

log = logging.getLogger(__name__)

DIST_DIR = CONFIG_DIR / "dist"
# Base64 adds a third; keep each reply under the 1 MiB default message limit of WebSocket clients.
FILE_CHUNK = 512 << 10
MAX_MESSAGE = 4 << 20
# Agents may send clipboard items (images) up to 16 MiB, base64-encoded.
MAX_AGENT_MESSAGE = 24 << 20


class Api:
    def __init__(self, hub):
        self.hub = hub  # service.Hub
        self.identity = hub.identity
        self.subscribers = set()
        self.agent_conns = {}  # connection -> agent id
        self.agent_caps = {}   # connection -> set of capabilities from the agent's hello
        self._tray_last = {}   # connection -> last status sent to that agent's tray
        self._agent_requests = {}  # request id -> future, for hub-initiated requests to agents
        self._next_request = 0
        self._local = self._agent = self._pairing = None
        self._agent_ctx = None

    async def start(self):
        SOCKET_PATH.unlink(missing_ok=True)
        self._local = await unix_serve(self._handle_local, str(SOCKET_PATH), max_size=MAX_MESSAGE)
        os.chmod(SOCKET_PATH, 0o600)
        self._agent_ctx = self.identity.server_context(require_client_cert=True)
        self._agent = await serve(self._handle_agent, "0.0.0.0", self.hub.config["api_port"],
                                  ssl=self._agent_ctx, max_size=MAX_AGENT_MESSAGE)
        log.info("api: local %s, agents on :%d", SOCKET_PATH, self.hub.config["api_port"])

    async def stop(self):
        for server in (self._local, self._agent, self._pairing):
            if server is not None:
                server.close()
                await server.wait_closed()
        SOCKET_PATH.unlink(missing_ok=True)

    # --- state push ----------------------------------------------------------

    def broadcast(self, state):
        msg = json.dumps(state)
        for conn in list(self.subscribers):
            asyncio.ensure_future(self._send(conn, msg))
        self._push_tray_status(state)

    def _push_tray_status(self, state):
        """Agents with a tray get a small status (who holds the devices, what they can switch to),
        only when it changes."""
        for conn, agent_id in list(self.agent_conns.items()):
            if "tray" not in self.agent_caps.get(conn, ()):
                continue
            agent = self.identity.agents().get(agent_id) or {}
            status = {"type": "status", "hub": socket.gethostname(), "self": agent.get("target"),
                      "holder": state["active"], "pending": state["pending"],
                      "targets": [{k: t[k] for k in ("id", "name", "kind", "connected")} for t in state["targets"]]}
            data = json.dumps(status)
            if self._tray_last.get(conn) != data:
                self._tray_last[conn] = data
                asyncio.ensure_future(self._send(conn, data))

    @staticmethod
    async def _send(conn, msg):
        try:
            await conn.send(msg)
        except ConnectionClosed:
            pass

    def online_agents(self):
        return set(self.agent_conns.values())

    def agent_capabilities(self):
        """agent id -> capabilities, for connected agents."""
        return {aid: sorted(self.agent_caps.get(conn, ())) for conn, aid in self.agent_conns.items()}

    def send_to_agent(self, agent_id, msg):
        data = json.dumps(msg)
        for conn, aid in list(self.agent_conns.items()):
            if aid == agent_id:
                asyncio.ensure_future(self._send(conn, data))

    async def request_agent(self, agent_id, msg, timeout):
        """Send a request to an agent and wait for its reply (matched by "re")."""
        self._next_request += 1
        msg = {**msg, "id": self._next_request}
        fut = asyncio.get_running_loop().create_future()
        self._agent_requests[msg["id"]] = fut
        try:
            self.send_to_agent(agent_id, msg)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._agent_requests.pop(msg["id"], None)

    def agent_for_target(self, target_id, capability):
        for conn, agent_id in self.agent_conns.items():
            agent = self.identity.agents().get(agent_id)
            if agent and agent["target"] == target_id and capability in self.agent_caps.get(conn, ()):
                return agent_id
        return None

    def send_to_agents(self, msg, capability, exclude=None):
        data = json.dumps(msg)
        for conn, agent_id in list(self.agent_conns.items()):
            if agent_id != exclude and capability in self.agent_caps.get(conn, ()):
                asyncio.ensure_future(self._send(conn, data))

    # --- connections ---------------------------------------------------------

    async def _handle_local(self, conn):
        await self._serve(conn, local=True, agent=None)

    async def _handle_agent(self, conn):
        ssl_obj = conn.transport.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
        agent = self.identity.agent_by_fingerprint(hashlib.sha256(der).hexdigest()) if der else None
        if agent is None:  # trusted by the TLS layer but since unpaired
            await conn.close(4003, "not paired")
            return
        self.agent_conns[conn] = agent["id"]
        self.hub.state_changed()
        try:
            await self._serve(conn, local=False, agent=agent)
        finally:
            self.agent_conns.pop(conn, None)
            self.agent_caps.pop(conn, None)
            self._tray_last.pop(conn, None)
            if agent["id"] not in self.agent_conns.values():
                self.hub.remote.agent_gone(agent["id"])
                self.hub.notifications.close_all(agent["id"])
                self.hub.notifications.call(agent, {"state": "ended"})
            self.hub.state_changed()

    async def _serve(self, conn, local, agent):
        try:
            async for raw in conn:
                try:
                    req = json.loads(raw)
                    reply = await self._dispatch(conn, req, local, agent)
                except (ValueError, KeyError) as exc:  # bad request
                    log.warning("rejected request: %s", exc)
                    req = req if isinstance(req, dict) else {}
                    reply = {"type": "error", "error": str(exc)}
                except Exception as exc:  # report the error, keep the connection
                    log.exception("request failed")
                    req = req if isinstance(req, dict) else {}
                    reply = {"type": "error", "error": str(exc)}
                if reply is not None:
                    if "id" in req:
                        reply["id"] = req["id"]
                    await conn.send(json.dumps(reply))
        except ConnectionClosed:
            pass
        finally:
            self.subscribers.discard(conn)

    async def _dispatch(self, conn, req, local, agent):
        kind = req.get("type")
        if kind == "subscribe":
            self.subscribers.add(conn)
            return self.hub.state()
        if kind == "state":
            return self.hub.state()
        if kind == "switch":
            self.hub.router.switch(req.get("target"))
            return {"type": "ok"}
        if kind == "cycle":
            self.hub.router.cycle()
            return {"type": "ok"}
        if kind == "set_transfer" and str(req.get("device", "")).startswith("remote:"):
            if not self.hub.remote.set_transfer(req["device"], bool(req["enabled"])):
                return {"type": "error", "error": "unknown or unsupported device"}
            self.hub.state_changed()
            return {"type": "ok"}
        if kind == "set_transfer":
            dev = self.hub.pool.get(req["device"])
            if dev is None or not self.hub.pool.set_transfer(dev.id, bool(req["enabled"])):
                return {"type": "error", "error": "unknown or unsupported device"}
            self.hub.router.on_transfer_changed(dev)
            self.hub.state_changed()
            return {"type": "ok"}
        if agent is not None and kind == "hello":
            self.agent_caps[conn] = set(req.get("capabilities", []))
            log.info("agent %s (%s %s) capabilities %s", agent["name"], req.get("platform"),
                     req.get("version"), sorted(self.agent_caps[conn]))
            if isinstance(req.get("features"), list):  # apps with switchable features (Android)
                self.hub.agent_settings.seen(agent["id"], req["features"], req.get("version"))
                await conn.send(json.dumps(self.hub.agent_settings.config_message(agent["id"])))
            self.hub.state_changed()
            if "clipboard" in self.agent_caps[conn]:
                current = self.hub.clipboard.for_new_agent()
                if current:
                    await conn.send(json.dumps(current))
            # The Pico's USB identity, so a Windows agent never forwards the Pico's own Xbox interface back.
            return {"type": "welcome", "agent_id": agent["id"], "pico_usb_id": ":".join(PICO_USB_ID)}
        if agent is not None and kind == "capabilities":  # features started/stopped after a config push
            self.agent_caps[conn] = set(req.get("capabilities", []))
            if "notifications" not in self.agent_caps[conn]:
                self.hub.notifications.close_all(agent["id"])
            if "calls" not in self.agent_caps[conn]:
                self.hub.notifications.call(agent, {"state": "ended"})
            self.hub.state_changed()
            return None
        if agent is not None and kind == "call":
            if "calls" in self.agent_caps.get(conn, ()):
                self.hub.notifications.call(agent, req)
            return None
        if agent is not None and kind == "mirror_action":
            if "notification_mirror" in self.agent_caps.get(conn, ()):
                self.hub.notifications.mirror_action(req)
            return None
        if agent is not None and kind == "notification_apps":  # the device's apps, for the GUI's filter
            from .notifications import save_app_icons
            apps = save_app_icons(req.get("apps", []))
            self.hub.agent_settings.set_catalog(agent["id"], "notifications", apps)
            self.hub.state_changed()
            return None
        if agent is not None and kind in ("notification", "notification_removed"):
            if "notifications" in self.agent_caps.get(conn, ()):
                if kind == "notification":
                    self.hub.notifications.posted(agent, req)
                else:
                    self.hub.notifications.removed(agent, req.get("key"))
            return None
        if agent is not None and kind == "devices":
            self.hub.remote.agent_devices(agent, req.get("devices", []))
            self.hub.state_changed()
            return None
        if agent is not None and kind == "pad":
            self.hub.remote.pad_state(agent["id"], req)
            return None
        if agent is not None and kind == "pico_flash_result":
            fut = self._agent_requests.get(req.get("re"))
            if fut is not None and not fut.done():
                fut.set_result(req)
            return None
        if agent is not None and kind == "clipboard":
            self.hub.clipboard.from_agent(agent["id"], req)
            return None
        if kind == "get_update":
            return self._manifest(req["platform"])
        if kind == "get_file":
            return self._file_chunk(req["platform"], req["path"], int(req.get("offset", 0)))
        if local and kind == "set_agent_feature":
            agent_id = str(req["agent"])
            self.hub.agent_settings.set_feature(agent_id, str(req["feature"]), bool(req["enabled"]))
            self.send_to_agent(agent_id, self.hub.agent_settings.config_message(agent_id))
            self.hub.state_changed()
            return {"type": "ok"}
        if local and kind == "set_feature_settings":
            agent_id = str(req["agent"])
            self.hub.agent_settings.set_settings(agent_id, str(req["feature"]), req.get("settings"))
            self.send_to_agent(agent_id, self.hub.agent_settings.config_message(agent_id))
            self.hub.state_changed()
            return {"type": "ok"}
        if local and kind == "set_desktop_notifications":
            self.hub.notifications.set_desktop(bool(req["enabled"]))
            self.hub.state_changed()
            return {"type": "ok"}
        if local and kind == "set_appearance":
            self.hub.agent_settings.set_appearance(req.get("pc_theme"), req.get("device_theme"))
            for agent_id in self.hub.agent_settings.configured():
                self.send_to_agent(agent_id, self.hub.agent_settings.config_message(agent_id))
            self.hub.state_changed()
            return {"type": "ok"}
        if local and kind == "clipboard_restore":
            self.hub.clipboard.restore(str(req["item"]))
            return {"type": "ok"}
        if local and kind == "clipboard_get":
            return self.hub.clipboard.get(str(req["item"]))
        if local and kind == "clipboard_remove":
            self.hub.clipboard.remove(req.get("item"))
            return {"type": "ok"}
        if local and kind == "pair_start":
            return await self.pair_start(req["target"])
        if local and kind == "ble_pair":
            channels = self.hub.bluetooth_channels()
            if not channels:
                return {"type": "error", "error": "no Bluetooth channel configured"}
            for ble in channels:
                ble.start_pairing(int(req.get("seconds", 120)))
            return {"type": "ok"}
        if local and kind == "forget_target":
            self.hub.forget_target(req["target"])
            return {"type": "ok"}
        if local and kind == "rename_target":
            self.hub.rename_target(req["target"], str(req["name"])[:40])
            return {"type": "ok"}
        if local and kind == "flash_pico":
            return await self.flash_pico(req["target"], req["uf2"])
        if local and kind == "install_android":
            return await self.install_android(req["target"], bool(req.get("pair")))
        if local and kind == "install_agent":
            return await self.install_agent(req["target"], req.get("layout") or env.get("SWITCHER_INSTALL_LAYOUT", "us"))
        if local and kind == "unpair":
            ok = self.identity.remove_agent(req["agent"])
            if ok:
                await self._reload_agent_server()
                self.hub.state_changed()
            return {"type": "ok" if ok else "error"}
        return {"type": "error", "error": f"unsupported request {kind!r}"}

    # --- pairing -------------------------------------------------------------

    async def pair_start(self, target):
        if target not in self.hub.targets:
            return {"type": "error", "error": f"unknown target {target}"}
        token = self.identity.new_pairing_token(target)
        if self._pairing is None:
            self._pairing = await serve(self._handle_pairing, "0.0.0.0", self.hub.config["pairing_port"],
                                        ssl=self.identity.server_context(require_client_cert=False),
                                        process_request=self._bootstrap_http)
            asyncio.ensure_future(self._close_pairing_when_idle())
            log.info("pairing port %d open", self.hub.config["pairing_port"])
        return {"type": "pairing", "token": token, "hub_fingerprint": self.identity.fingerprint,
                "hub_cert": self.identity.cert_pem, "hosts": local_addresses(),
                "pairing_port": self.hub.config["pairing_port"], "api_port": self.hub.config["api_port"]}

    def _bootstrap_http(self, conn, request):
        """Plain HTTPS GETs on the pairing port: the agent bootstrap, only with a live pairing token."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None  # a pairing WebSocket
        parts = request.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "agent" or not self.identity.pairing_token_valid(parts[1]):
            return Response(404, "Not Found", Headers(), b"not found\n")
        token, name = parts[1], parts[2]
        if name == "bootstrap.ps1":
            from .bootstrap import windows_bootstrap
            body = windows_bootstrap(token, self.identity.fingerprint, local_addresses(),
                                     self.hub.config["pairing_port"]).encode("utf-8-sig")
            ctype = "text/plain; charset=utf-8"
        elif name == "agent-windows.zip" and (DIST_DIR / "agent-windows.zip").is_file():
            body = (DIST_DIR / "agent-windows.zip").read_bytes()
            ctype = "application/zip"
        else:
            return Response(404, "Not Found", Headers(), b"not found\n")
        return Response(200, "OK", Headers([("Content-Type", ctype), ("Content-Length", str(len(body)))]), body)

    async def flash_pico(self, target_id, uf2_b64):
        """Reboot a target's Pico into its bootloader and have that machine's agent copy the firmware on."""
        from . import protocol
        target = self.hub.targets.get(target_id)
        if target is None or target.kind != "pico":
            return {"type": "error", "error": "not a Pico target"}
        agent_id = self.agent_for_target(target_id, "pico-flash")
        if agent_id is None:
            return {"type": "error", "error": f"no online agent on {target.name} that can flash the Pico"}
        if self.hub.router.active == target_id:
            self.hub.router.switch(None)  # its USB is about to disappear
        flashing = asyncio.ensure_future(self.request_agent(agent_id, {"type": "pico_flash", "data": uf2_b64}, 40))
        await asyncio.sleep(0.5)  # agent is now watching for the drive
        target.send_raw_frame(protocol.MSG_BOOTLOADER)
        reply = await flashing
        return {"type": "ok" if reply.get("ok") else "error", **{k: v for k, v in reply.items() if k in ("drive", "error")}}

    async def install_agent(self, target_id, layout):
        """Type the bootstrap command on a USB target: PowerShell fetches, installs and pairs the agent."""
        from .bootstrap import windows_command
        from .typing import Typist
        target = self.hub.targets.get(target_id)
        if target is None or target.kind != "pico":
            return {"type": "error", "error": "agent install needs a USB (Pico) target"}
        if not target.connected:
            return {"type": "error", "error": f"{target.name} is not connected"}
        if not (DIST_DIR / "agent-windows.zip").is_file():
            return {"type": "error", "error": "no agent build; run tools/build-agent-windows.sh"}
        if self.hub.router.active == target_id:
            return {"type": "error", "error": "switch your keyboard/mouse back to Linux first"}
        typist = Typist(target, layout)  # validates the layout
        pairing = await self.pair_start(target_id)
        hosts = pairing["hosts"]
        if not hosts:
            return {"type": "error", "error": "no network address to give the agent"}
        command = windows_command(pairing["token"], pairing["hub_fingerprint"], hosts[0], pairing["pairing_port"])

        async def run():
            try:
                await typist.run_dialog("powershell -NoProfile -ExecutionPolicy Bypass")
                await asyncio.sleep(3)  # PowerShell window opening
                await typist.type(command + "\n")
                log.info("agent install command typed on %s", target.name)
            except Exception:
                log.exception("typing the install command failed")
        asyncio.ensure_future(run())
        return {"type": "ok", "chars": len(command)}

    async def install_android(self, target_id, pair=False):
        """Install or update the Android agent over ADB on the device behind a Bluetooth target. Pairs it
        when the target has no agent yet (or when asked), so updates don't pile up agent records."""
        from . import android
        target = self.hub.targets.get(target_id)
        if target is None or target.kind != "ble":
            return {"type": "error", "error": "the Android app is for Bluetooth (phone/tablet) targets"}
        apk = DIST_DIR / "agent-android.apk"
        if not apk.is_file():
            return {"type": "error", "error": "no Android build; run tools/build-android.sh"}

        async def start_pairing(device_ip):
            pairing = await self.pair_start(target_id)
            host = address_facing(device_ip, pairing["hosts"]) if device_ip else (pairing["hosts"] or [None])[0]
            if not host:
                raise android.AdbError("no network address to give the app")
            return host, pairing["pairing_port"], pairing["token"], pairing["hub_fingerprint"]
        paired = any(a["target"] == target_id and a["platform"] == "android" for a in self.identity.agents().values())
        try:
            serial = await android.install(apk, target.address, start_pairing if (pair or not paired) else None)
        except android.AdbError as exc:
            return {"type": "error", "error": str(exc)}
        return {"type": "ok", "device": serial, "paired": pair or not paired}

    async def _handle_pairing(self, conn):
        try:
            req = json.loads(await asyncio.wait_for(conn.recv(), 10))
            target = self.identity.consume_pairing_token(str(req.get("token", "")))
            if req.get("type") != "pair" or target is None:
                await conn.send(json.dumps({"type": "error", "error": "invalid or expired token"}))
                return
            agent = self.identity.add_agent(str(req["name"])[:64], str(req["platform"])[:32],
                                            req["cert_pem"], target)
            self._agent_ctx.load_verify_locations(cadata=agent["cert_pem"])
            log.info("paired agent %s (%s) for target %s", agent["name"], agent["id"], target)
            await conn.send(json.dumps({"type": "paired", "agent_id": agent["id"],
                                        "sign_public_key": self.identity.sign_public_b64,
                                        "api_port": self.hub.config["api_port"]}))
            self.hub.state_changed()
        except (ConnectionClosed, asyncio.TimeoutError, KeyError, ValueError):
            pass

    async def _close_pairing_when_idle(self):
        while self.identity.pairing_active():
            await asyncio.sleep(5)
        if self._pairing is not None:
            self._pairing.close()
            await self._pairing.wait_closed()
            self._pairing = None
            log.info("pairing port closed")

    async def _reload_agent_server(self):
        """Removing a trusted certificate needs a fresh TLS context, so restart the agent listener."""
        for conn, agent_id in list(self.agent_conns.items()):
            if agent_id not in self.identity.agents():
                await conn.close(4003, "unpaired")
        self._agent.close()
        await self._agent.wait_closed()
        self._agent_ctx = self.identity.server_context(require_client_cert=True)
        self._agent = await serve(self._handle_agent, "0.0.0.0", self.hub.config["api_port"],
                                  ssl=self._agent_ctx, max_size=MAX_AGENT_MESSAGE)

    # --- agent updates -------------------------------------------------------

    def _dist(self, platform):
        base = DIST_DIR / platform
        if not platform.isidentifier() or not base.is_dir():
            raise ValueError(f"no agent build for {platform!r}")
        return base

    def _manifest(self, platform):
        base = self._dist(platform)
        files = []
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            files.append({"path": path.relative_to(base).as_posix(), "size": path.stat().st_size,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        version_file = base / "VERSION"
        manifest = json.dumps({"platform": platform, "files": files,
                               "version": version_file.read_text().strip() if version_file.exists() else "0"},
                              sort_keys=True)
        return {"type": "update", "manifest": manifest, "signature": self.identity.sign(manifest.encode())}

    def _file_chunk(self, platform, rel, offset):
        base = self._dist(platform).resolve()
        path = (base / rel).resolve()
        if base not in path.parents or not path.is_file():
            raise ValueError("bad path")
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(FILE_CHUNK)
        return {"type": "file", "path": rel, "offset": offset, "size": path.stat().st_size,
                "data": base64.b64encode(data).decode()}


TUNNEL_PREFIXES = ("lo", "tun", "tap", "wg", "proton", "docker", "br-", "veth", "virbr", "tailscale", "zt")


def local_addresses():
    """IPv4 addresses agents can use to reach the hub, best first: SWITCHER_HUB_ADDRESS if set, else LAN
    interfaces only. VPN tunnels are skipped: with a VPN up the default route goes through the tunnel, whose
    address (often the same one on every client) is useless to another device on the LAN."""
    import ipaddress
    import subprocess
    if env.get("SWITCHER_HUB_ADDRESS"):
        return [env.get("SWITCHER_HUB_ADDRESS")]
    found = []
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=5).stdout
        for iface in json.loads(out or "[]"):
            name = iface.get("ifname", "")
            if name.startswith(TUNNEL_PREFIXES) or "UP" not in iface.get("flags", []):
                continue
            for a in iface.get("addr_info", []):
                if a.get("family") == "inet" and a.get("prefixlen", 32) < 32:
                    found.append(a["local"])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if not found:  # fall back to the default-route address
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))  # no packet is sent; picks the default-route interface
                found.append(s.getsockname()[0])
        except OSError:
            pass
    return sorted(set(found), key=lambda a: (not ipaddress.ip_address(a).is_private, a))


def address_facing(device_ip, addresses):
    """The hub address on the same /24 as a device, else the first one."""
    import ipaddress
    try:
        net = ipaddress.ip_network(f"{device_ip}/24", strict=False)
        return next((a for a in addresses if ipaddress.ip_address(a) in net), addresses[0] if addresses else None)
    except ValueError:
        return addresses[0] if addresses else None
