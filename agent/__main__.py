"""pi-switcher-hub agent.

  python -m agent pair --hub HOST --port PORT --token TOKEN --fingerprint SHA256
  python -m agent run
"""
import argparse
import asyncio
import logging
import os
import sys

from .state import State


def main():
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair")
    p.add_argument("--hub", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--fingerprint", required=True)
    sub.add_parser("run")
    args = ap.parse_args()

    state = State()
    log_file = state.dir / "agent.log"
    handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)

    if args.cmd == "pair":
        from .pairing import pair
        agent_id = asyncio.run(pair(state, args.hub, args.port, args.token, args.fingerprint))
        print(f"paired with hub {args.hub} as {agent_id}")
        return

    if not state.paired:
        sys.exit("not paired; run the install command from the hub again")
    clipboard = pads = None
    if sys.platform == "win32":
        from .clipboard_win import WindowsClipboard
        clipboard = WindowsClipboard()
        try:
            from .gamepad_win import XInputPads
            pads = XInputPads()
        except OSError as exc:
            logging.warning("gamepads unavailable: %s", exc)
    tray = None
    if sys.platform == "win32":
        from .state import version
        from .tray_win import WindowsTray
        try:
            tray = WindowsTray(state.dir / "icons", log_file, version(), on_switch=lambda t: agent.switch(t),
                               on_quit=lambda: os._exit(0))
            tray.start()
            if tray.hwnd is None:
                tray = None
        except Exception:
            logging.exception("tray icon unavailable")
            tray = None
    features = None
    if tray is not None:
        from .features import FeatureSet, NotificationMirror
        features = FeatureSet([NotificationMirror(tray, state.dir / "toasts")], state.dir / "features.json")
    from .client import Agent
    agent = Agent(state, clipboard, pads=pads, tray=tray, features=features)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
