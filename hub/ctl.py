"""Command-line client for the hub service (over the local Unix socket).

  python3 -m hub.ctl status
  python3 -m hub.ctl switch laptop|linux
  python3 -m hub.ctl transfer DEVICE_ID on|off
  python3 -m hub.ctl pair TARGET
  python3 -m hub.ctl ble-pair [SECONDS]      (let a new phone/tablet pair; it becomes a new target)
  python3 -m hub.ctl ble-confirm [--reject]  (approve the pairing code once it matches the phone/tablet)
  python3 -m hub.ctl forget TARGET           (unpair a Bluetooth target)
  python3 -m hub.ctl rename TARGET NAME
  python3 -m hub.ctl install-agent TARGET [--layout us|uk] (types the agent install command on it)
  python3 -m hub.ctl install-android TARGET [--pair]      (installs/updates the Android app over USB/ADB;
                                                           pairs on first install, or again with --pair)
  python3 -m hub.ctl flash-pico TARGET [UF2]              (flash the Pico through that machine's agent)
  python3 -m hub.ctl watch
"""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

from websockets.asyncio.client import unix_connect

from .config import SOCKET_PATH


def print_state(s):
    where = s["active"] or "linux"
    if s["pending"]:
        where += f" (switching to {s['pending']} once keys are released)"
    print(f"devices are on: {where}\n")
    print("targets:")
    for t in s["targets"]:
        if t["connected"]:
            status = "connected"
        elif t.get("responding") and t["kind"] == "ble":
            status = "not connected (out of range or Bluetooth off?)" if t.get("address") else "waiting for its first connection"
        elif t.get("responding"):
            status = "Pico answers, but the target hasn't detected it over USB (data cable? port?)"
        elif t.get("responding") is False and t["link"] and t["kind"] == "ble":
            status = "ESP32 not answering (router firmware flashed? EN jumper removed?)"
        elif t["link"]:
            status = "serial link open, Pico not answering (power? wiring?)"
        else:
            status = "serial adapter not found"
        extra = ", suspended" if t["suspended"] else ""
        if t.get("conn_interval_ms") and t["connected"]:
            extra += f", interval {t['conn_interval_ms']:g} ms"
        print(f"  {t['id']:<10} {t['name']:<12} [{t['kind']}] {status}{extra}")
    for b in s.get("bluetooth", []):
        line = "ready" if b["responding"] else "ESP32 not answering"
        if b["pairing"]:
            line += ", PAIRING OPEN"
        if b.get("passkey"):
            line += f", pairing code {b['passkey']}: if it matches the phone/tablet, run ble-confirm"
        print(f"  bluetooth  {line}")
    print("\ndevices:")
    for d in s["devices"]:
        toggle = "transfer" if d["transfer"] else ("local   " if d["supported"] else "n/a     ")
        origin = "" if d.get("origin", "linux") == "linux" else f" (on {d['origin']}{', forwarding' if d.get('grabbed') else ''})"
        print(f"  [{toggle}] {d['name'] + origin:<48} {','.join(d['kinds']):<16} {d['id']}")
    for p in s.get("passthrough", []):
        print(f"\nnote: passthrough device on this machine (ignored, never forwarded): {p}")
    if s["agents"]:
        print("\nagents:")
        for a in s["agents"]:
            print(f"  {a['id']}  {a['name']} ({a['platform']}) -> {a['target']}  {'online' if a['online'] else 'offline'}")


async def request(msg):
    async with unix_connect(str(SOCKET_PATH)) as ws:
        await ws.send(json.dumps(msg))
        return json.loads(await ws.recv())


async def watch():
    async with unix_connect(str(SOCKET_PATH)) as ws:
        await ws.send(json.dumps({"type": "subscribe"}))
        async for raw in ws:
            print("\033[2J\033[H", end="")
            print_state(json.loads(raw))


def main():
    ap = argparse.ArgumentParser(prog="hub.ctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("watch")
    p = sub.add_parser("switch")
    p.add_argument("target", help="target id, or 'linux'")
    p = sub.add_parser("transfer")
    p.add_argument("device")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("pair")
    p.add_argument("target")
    p = sub.add_parser("ble-pair")
    p.add_argument("seconds", nargs="?", type=int, default=120)
    p = sub.add_parser("ble-confirm")
    p.add_argument("--reject", action="store_true")
    p = sub.add_parser("forget")
    p.add_argument("target")
    p = sub.add_parser("flash-pico")
    p.add_argument("target")
    p.add_argument("uf2", nargs="?", default=str(Path(__file__).resolve().parent.parent / "firmware/build/switcher_hub.uf2"))
    p = sub.add_parser("install-agent")
    p.add_argument("target")
    p.add_argument("--layout", default=None, help="us or uk (default: SWITCHER_INSTALL_LAYOUT)")
    p = sub.add_parser("install-android")
    p.add_argument("target")
    p.add_argument("--pair", action="store_true", help="pair again even if already paired")
    p = sub.add_parser("rename")
    p.add_argument("target")
    p.add_argument("name")
    args = ap.parse_args()

    try:
        if args.cmd == "status":
            print_state(asyncio.run(request({"type": "state"})))
        elif args.cmd == "watch":
            asyncio.run(watch())
        elif args.cmd == "switch":
            target = None if args.target == "linux" else args.target
            print(asyncio.run(request({"type": "switch", "target": target})))
        elif args.cmd == "transfer":
            print(asyncio.run(request({"type": "set_transfer", "device": args.device, "enabled": args.state == "on"})))
        elif args.cmd == "ble-pair":
            print(asyncio.run(request({"type": "ble_pair", "seconds": args.seconds})))
        elif args.cmd == "ble-confirm":
            print(asyncio.run(request({"type": "ble_confirm", "accept": not args.reject})))
        elif args.cmd == "forget":
            print(asyncio.run(request({"type": "forget_target", "target": args.target})))
        elif args.cmd == "flash-pico":
            data = base64.b64encode(Path(args.uf2).read_bytes()).decode()
            print(asyncio.run(request({"type": "flash_pico", "target": args.target, "uf2": data})))
        elif args.cmd == "install-agent":
            print(asyncio.run(request({"type": "install_agent", "target": args.target, "layout": args.layout})))
        elif args.cmd == "install-android":
            print(asyncio.run(request({"type": "install_android", "target": args.target, "pair": args.pair})))
        elif args.cmd == "rename":
            print(asyncio.run(request({"type": "rename_target", "target": args.target, "name": args.name})))
        elif args.cmd == "pair":
            print(json.dumps(asyncio.run(request({"type": "pair_start", "target": args.target})), indent=2))
    except (FileNotFoundError, ConnectionRefusedError):
        sys.exit("hub service is not running")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
