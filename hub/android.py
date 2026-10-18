"""Installing the Android agent over ADB (USB): the device is matched to its Bluetooth target by address,
the permissions that clipboard reading needs are granted, and the app is paired with that target."""
import asyncio
import logging
import re

from . import env

log = logging.getLogger(__name__)

# The app's ID on devices (SWITCHER_ANDROID_APP_ID, also read by android/app/build.gradle.kts) and the
# Java/Kotlin package its components live in (fixed: it's the source tree, not configuration).
PACKAGE = env.get("SWITCHER_ANDROID_APP_ID", "dev.switcher.app")
CODE = "online.thrax.switcher"


class AdbError(Exception):
    pass


async def adb(*args, serial=None, timeout=60):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        raise AdbError("adb is not installed (pacman -S android-tools)")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise AdbError(f"adb {' '.join(args[:2])} timed out")
    return proc.returncode, out.decode(errors="replace").strip()


async def find_device(address):
    """Serial of the USB device whose Bluetooth address is `address` (or the only device, if the target
    has no address yet)."""
    _, out = await adb("devices")
    devices, unauthorized = [], []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            (devices if parts[1] == "device" else unauthorized).append(parts[0])
    if not devices:
        if unauthorized:
            raise AdbError("tap 'Allow USB debugging' on the device first")
        raise AdbError("no device connected over USB (USB debugging on?)")
    if address:
        for serial in devices:
            _, bt = await adb("shell", "settings", "get", "secure", "bluetooth_address", serial=serial)
            if bt.strip().lower() == address.lower():
                return serial
        raise AdbError("none of the connected USB devices has this target's Bluetooth address")
    if len(devices) == 1:
        return devices[0]
    raise AdbError("several devices connected; unplug the others")


async def allow_past_vpn_lockdown(serial):
    """Put the app on the always-on VPN's lockdown allowlist, keeping whatever else is there.

    With "Block connections without VPN" on, Android blocks every app's traffic outside the VPN, LAN
    included, and apps the VPN excludes (split tunnelling) too; only allowlisted apps get past. Android
    reads the list when the always-on VPN starts, so it applies after the next reboot. Toggling lockdown
    in Settings rewrites the list, which is why each install puts us back on it.
    """
    _, current = await adb("shell", "settings", "get", "secure", "always_on_vpn_lockdown_whitelist", serial=serial)
    apps = [p for p in current.strip().split(",") if p and p != "null"]
    if PACKAGE not in apps:
        apps.append(PACKAGE)
        await adb("shell", "settings", "put", "secure", "always_on_vpn_lockdown_whitelist", ",".join(apps),
                  serial=serial)
        log.info("added the app to the VPN lockdown allowlist on %s (applies after a reboot)", serial)


async def install(apk, address, start_pairing):
    """Install/update the app on the device for a target and grant its permissions; pair it unless
    start_pairing is None (an update keeps the app's existing pairing).
    start_pairing(device_wifi_ip) -> (host, port, token, fingerprint). Returns the device serial."""
    serial = await find_device(address)
    # "-i <itself>": the app becomes its own installer of record, which lets Android 12+ apply its
    # Wi-Fi self-updates without asking. A first install can't name a package that isn't there yet.
    rc, out = await adb("install", "-r", "-i", PACKAGE, str(apk), serial=serial, timeout=120)
    if rc != 0 or "Success" not in out:
        rc, out = await adb("install", "-r", str(apk), serial=serial, timeout=120)
    if rc != 0 or "Success" not in out:
        raise AdbError(f"install failed: {out[-300:]}")
    # READ_LOGS lets the app notice copies, the overlay lets it read them (Android 10+ restricts
    # clipboard reads to the focused app); battery exemption keeps the connection alive.
    steps = [("shell", "pm", "grant", PACKAGE, "android.permission.READ_LOGS"),
             ("shell", "appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "allow"),
             ("shell", "appops", "set", PACKAGE, "REQUEST_INSTALL_PACKAGES", "allow"),  # self-updates
             ("shell", "dumpsys", "deviceidle", "whitelist", f"+{PACKAGE}")]
    for step in steps:
        rc, out = await adb(*step, serial=serial)
        if rc != 0:
            raise AdbError(f"{' '.join(step[1:3])} failed: {out[-200:]}")
    await adb("shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS", serial=serial)  # Android 13+
    # Calls feature (fails harmlessly on devices without telephony, e.g. a Wi-Fi tablet).
    for perm in ("READ_PHONE_STATE", "READ_CALL_LOG", "READ_CONTACTS", "ANSWER_PHONE_CALLS"):
        await adb("shell", "pm", "grant", PACKAGE, f"android.permission.{perm}", serial=serial)
    # Notification access for the notifications feature. Its listener component stays disabled until the
    # feature is switched on from the PC, so granting this doesn't make the app read anything by itself.
    # Right after an install Android may not have re-scanned the app yet and silently ignores the grant,
    # so check it stuck and retry.
    listener = f"{PACKAGE}/{CODE}.features.NotificationListener"
    for _ in range(5):
        await adb("shell", "cmd", "notification", "allow_listener", listener, serial=serial)
        _, granted = await adb("shell", "settings", "get", "secure", "enabled_notification_listeners", serial=serial)
        if listener in granted.split(":"):
            break
        await asyncio.sleep(1)
    else:
        log.warning("notification access not granted on %s; the app's Notifications card can open the setting",
                    serial)
    await allow_past_vpn_lockdown(serial)
    # The ESP32 keeps every Bluetooth host connected (instant switching), so Android always sees a physical
    # keyboard and would hide the on-screen one; keep it available.
    await adb("shell", "settings", "put", "secure", "show_ime_with_hard_keyboard", "1", serial=serial)
    # Restart the app so the new process gets READ_LOGS (it's applied at process start).
    await adb("shell", "am", "force-stop", PACKAGE, serial=serial)
    if start_pairing is None:
        # Bring it up on screen: Android 13+ only asks for log access while the app is visible.
        await adb("shell", "am", "start", "-n", f"{PACKAGE}/{CODE}.MainActivity", serial=serial)
        log.info("android agent updated on %s", serial)
        return serial
    _, wlan = await adb("shell", "ip", "-4", "-o", "addr", "show", "wlan0", serial=serial)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", wlan)
    host, port, token, fingerprint = await start_pairing(m.group(1) if m else None)
    rc, out = await adb("shell", "am", "start", "-n", f"{PACKAGE}/{CODE}.PairActivity", "--es", "host", host,
                        "--ei", "port", str(port), "--es", "token", token, "--es", "fingerprint", fingerprint,
                        serial=serial)
    if rc != 0 or "Error" in out:
        raise AdbError(f"could not start pairing: {out[-300:]}")
    log.info("android agent installed on %s, pairing", serial)
    return serial
