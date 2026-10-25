"""The optional OLED on the ESP32: which machine holds the keyboard and mouse, and who is connected.

The hub draws the 128x64 picture and the ESP32 only shows it, so the look can change without reflashing.
A picture is sent as 32 chunks of 32 bytes (SSD1306 page layout); the last chunk shows it. The whole picture
is resent every second: it survives an ESP32 reset, and the ESP32 shows "HUB OFFLINE" when the pictures stop.
Switching slides the new machine in. Without Pillow or the bundled fonts the display stays dark.
"""
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import protocol

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # the display is optional
    Image = None

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 128, 64
PANEL_HEIGHT = 46        # machine panel; the dock of machine icons below it
DOCK_HEIGHT = HEIGHT - PANEL_HEIGHT
SLOTS = 6                # machines the dock has room for
CHUNK = 32
CHUNKS = WIDTH * HEIGHT // 8 // CHUNK
RESEND_SECONDS = 1.0
SLIDE_STEPS = 8
SLIDE_SECONDS = 0.24
LOCAL_NAME = "Linux PC"
FONTS = Path(__file__).resolve().parent.parent / "gui" / "fonts"


@dataclass(frozen=True)
class Machine:
    id: object          # target id; None = this Linux PC
    name: str
    icon: str           # pc, laptop, phone, tablet
    connected: bool
    detail: str         # one line under the name


@dataclass(frozen=True)
class Scene:
    machines: tuple
    holder: object = None
    pairing: bool = False
    passkey: str = None

    @property
    def panel(self):
        """What the big panel shows; a change slides in a new one."""
        if self.pairing:
            return ("pairing", self.passkey)
        return ("machine", self.holder)


# --- drawing -----------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def draw_icon(kind):
    """A 16x16 icon."""
    im = Image.new("1", (16, 16), 0)
    d = ImageDraw.Draw(im)
    if kind == "pc":
        d.rectangle((1, 2, 14, 10), outline=1)
        d.rectangle((3, 4, 12, 8), fill=1)
        d.line((8, 11, 8, 12), fill=1)
        d.line((4, 13, 11, 13), fill=1)
    elif kind == "laptop":
        d.rectangle((3, 3, 12, 9), outline=1)
        d.rectangle((5, 5, 10, 7), fill=1)
        d.line((2, 11, 13, 11), fill=1)
        d.line((1, 12, 14, 12), fill=1)
    elif kind == "tablet":
        d.rounded_rectangle((2, 2, 13, 13), radius=1, outline=1)
        d.rectangle((4, 4, 11, 10), fill=1)
        d.point((7, 12), fill=1)
    elif kind == "bluetooth":
        d.line((8, 1, 8, 14), fill=1)
        d.line((8, 1, 12, 4), fill=1)
        d.line((12, 4, 4, 11), fill=1)
        d.line((4, 4, 12, 11), fill=1)
        d.line((12, 11, 8, 14), fill=1)
    else:  # phone
        d.rounded_rectangle((4, 1, 11, 14), radius=1, outline=1)
        d.rectangle((6, 3, 9, 10), fill=1)
        d.line((6, 12, 9, 12), fill=1)
    return im


def stamp(img, icon, x, y, ink=1, dim=False):
    """Draw an icon's lit pixels; dim keeps every other one (offline machines look faded)."""
    pixels = icon.load()
    for iy in range(icon.height):
        for ix in range(icon.width):
            if pixels[ix, iy] and not (dim and (ix + iy) % 2):
                img.putpixel((x + ix, y + iy), ink)


def fit_name(draw, name, room):
    """The blackletter at the largest size that fits; else a small mono face, cut with an ellipsis."""
    for size in (22, 20, 18, 16, 14):
        face = font("grenze_gotisch.ttf", size)
        if draw.textlength(name, font=face) <= room:
            return face, name
    face = font("ibm_plex_mono_medium.ttf", 10)
    while len(name) > 1 and draw.textlength(name + "...", font=face) > room:
        name = name[:-1]
    return face, name + "..."


def draw_machine_panel(machine):
    im = Image.new("1", (WIDTH, PANEL_HEIGHT), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    im.paste(draw_icon(machine.icon).resize((32, 32), Image.NEAREST), (5, 7))
    face, name = fit_name(d, machine.name, WIDTH - 44 - 3)
    d.text((44, 23), name, font=face, fill=1, anchor="ls")
    d.text((45, 29), machine.detail[:20], font=font("ibm_plex_mono_regular.ttf", 8), fill=1)
    return im


def draw_pairing_panel(passkey):
    im = Image.new("1", (WIDTH, PANEL_HEIGHT), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    small = font("ibm_plex_mono_regular.ttf", 8)
    if passkey:
        d.text((WIDTH // 2, 5), "APPROVE ON PC", font=small, fill=1, anchor="mt")
        code = f"{passkey[:3]} {passkey[3:]}"
        d.text((WIDTH // 2, 36), code, font=font("ibm_plex_mono_medium.ttf", 22), fill=1, anchor="ms")
    else:
        im.paste(draw_icon("bluetooth").resize((32, 32), Image.NEAREST), (5, 7))
        d.text((44, 23), "Pairing", font=font("grenze_gotisch.ttf", 22), fill=1, anchor="ls")
        d.text((45, 29), "waiting...", font=small, fill=1)
    return im


def draw_panel(scene):
    if scene.pairing:
        return draw_pairing_panel(scene.passkey)
    machine = next((m for m in scene.machines if m.id == scene.holder), None)
    return draw_machine_panel(machine or Machine(scene.holder, LOCAL_NAME, "pc", True, ""))


def slot_geometry(count):
    """(first slot's left edge, slot width) for a dock of `count` machines, centred."""
    count = max(1, min(count, SLOTS))
    width = WIDTH // SLOTS
    return (WIDTH - width * count) // 2, width


def draw_dock(scene, highlight):
    """The row of machine icons; `highlight` is the (fractional) slot index the lit box sits on."""
    machines = scene.machines[:SLOTS]
    left, width = slot_geometry(len(machines))
    plain = Image.new("1", (WIDTH, DOCK_HEIGHT), 0)
    inverted = Image.new("1", (WIDTH, DOCK_HEIGHT), 1)
    for i, machine in enumerate(machines):
        x = left + i * width + (width - 16) // 2
        stamp(plain, draw_icon(machine.icon), x, 1, dim=not machine.connected)
        stamp(inverted, draw_icon(machine.icon), x, 1, ink=0, dim=not machine.connected)
    box = Image.new("1", (WIDTH, DOCK_HEIGHT), 0)
    x = round(left + highlight * width)
    ImageDraw.Draw(box).rounded_rectangle((x + 1, 0, x + width - 2, DOCK_HEIGHT - 1), radius=3, fill=1)
    return Image.composite(inverted, plain, box)


def holder_slot(scene):
    for i, machine in enumerate(scene.machines[:SLOTS]):
        if machine.id == scene.holder:
            return i
    return 0


def draw_screen(scene, old=None, progress=1.0):
    """The picture: `scene`, or a slide from `old` to it (progress 0..1)."""
    img = Image.new("1", (WIDTH, HEIGHT), 0)
    if old is None or progress >= 1.0:
        img.paste(draw_panel(scene), (0, 0))
        highlight = holder_slot(scene)
    else:
        shift = round(WIDTH * progress)
        img.paste(draw_panel(old), (-shift, 0))
        img.paste(draw_panel(scene), (WIDTH - shift, 0))
        highlight = holder_slot(old) + (holder_slot(scene) - holder_slot(old)) * progress
    ImageDraw.Draw(img).line((0, PANEL_HEIGHT - 1, WIDTH, PANEL_HEIGHT - 1), fill=1)
    img.paste(draw_dock(scene, highlight), (0, PANEL_HEIGHT))
    return img


def pack(img):
    """SSD1306 layout: 8 pages of 128 columns, one byte = 8 vertical pixels, least significant bit on top."""
    pixels = img.load()
    out = bytearray()
    for page in range(HEIGHT // 8):
        for x in range(WIDTH):
            byte = 0
            for bit in range(8):
                if pixels[x, page * 8 + bit]:
                    byte |= 1 << bit
            out.append(byte)
    return bytes(out)


def chunks(data):
    return [bytes((i,)) + data[i * CHUNK:(i + 1) * CHUNK] for i in range(CHUNKS)]


# --- feeding the ESP32 ----------------------------------------------------------------------------

def ease(t):
    return 1 - (1 - t) ** 3


class Display:
    """Sends the screen to every ESP32 the hub has."""

    def __init__(self, hub):
        self.hub = hub
        self.enabled = Image is not None and FONTS.is_dir()
        self.scene = None      # what the screen shows (or is sliding to)
        self._timer = None     # the resend tick
        self._slide = None     # pending slide step
        if not self.enabled:
            log.info("OLED display off: needs Pillow and %s", FONTS)

    def start(self):
        if self.enabled:
            self._tick()

    def stop(self):
        for handle in (self._timer, self._slide):
            if handle is not None:
                handle.cancel()
        self._timer = self._slide = None

    def _tick(self):
        if self._slide is None:
            self.update(resend=True)
        self._timer = self.hub.loop.call_later(RESEND_SECONDS, self._tick)

    def build_scene(self):
        hub = self.hub
        machines = [Machine(None, LOCAL_NAME, "pc", True, "this machine")]
        for target in hub.targets.values():
            if target.kind == "ble":
                interval = target.conn_interval_ms
                icon = "tablet" if any(word in target.name.lower() for word in ("tab", "pad")) else "phone"
                detail = (f"Bluetooth {interval:.1f} ms" if interval else
                          "Bluetooth" if target.connected else "offline")
            else:
                icon, detail = "laptop", "USB" if target.connected else "offline"
            machines.append(Machine(target.id, target.name, icon, target.connected, detail))
        channels = hub.bluetooth_channels()
        holder = hub.router.pending if hub.router.pending is not None else hub.router.active
        return Scene(tuple(machines), holder, pairing=any(c.pairing for c in channels),
                     passkey=next((c.passkey for c in channels if c.pairing and c.passkey), None))

    def update(self, resend=False):
        """Show the current state; slide if the machine (or pairing panel) changed."""
        if not self.enabled:
            return
        scene = self.build_scene()
        old, self.scene = self.scene, scene
        if old is not None and old == scene and not resend:
            return
        if old is not None and old.panel != scene.panel and self.hub.bluetooth_channels():
            self._animate(old, scene, 1)
        else:
            self._show(draw_screen(scene))

    def _animate(self, old, scene, step):
        if self._slide is not None:
            self._slide.cancel()
        self._slide = None
        progress = ease(step / SLIDE_STEPS)
        self._show(draw_screen(scene, old, progress))
        if step < SLIDE_STEPS:
            self._slide = self.hub.loop.call_later(SLIDE_SECONDS / SLIDE_STEPS, self._animate, old, scene, step + 1)

    def _show(self, img):
        data = pack(img)
        for channel in self.hub.bluetooth_channels():
            for chunk in chunks(data):
                channel.send_frame(protocol.MSG_DISPLAY, chunk)
