"""Windows clipboard through the Win32 API (ctypes): text and images.

Images are exchanged as PNG. Windows apps put either a registered "PNG" format (browsers, Office) or
a CF_DIB bitmap (screenshots, Paint) on the clipboard; we read both and write both.
"""
import ctypes
import io
import time
from ctypes import wintypes

TEXT = "text/plain;charset=utf-8"
PNG = "image/png"

CF_DIB = 8
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

CF_PNG = user32.RegisterClipboardFormatW("PNG")
# Password managers mark copies with these so clipboard monitors/history skip them.
CF_EXCLUDE = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
CF_HISTORY = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
CF_CLOUD = user32.RegisterClipboardFormatW("CanUploadToCloudClipboard")


class _Open:
    """OpenClipboard with retries: another app may hold it for a moment."""

    def __enter__(self):
        for _ in range(20):
            if user32.OpenClipboard(None):
                return self
            time.sleep(0.01)
        raise OSError("clipboard is busy")

    def __exit__(self, *exc):
        user32.CloseClipboard()


def _read_global(handle):
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, kernel32.GlobalSize(handle))
    finally:
        kernel32.GlobalUnlock(handle)


def _write_global(data):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def _dib_to_png(dib):
    from PIL import Image
    header_size = int.from_bytes(dib[0:4], "little")
    bits = int.from_bytes(dib[14:16], "little")
    compression = int.from_bytes(dib[16:20], "little")
    colors = int.from_bytes(dib[32:36], "little") or (1 << bits if bits <= 8 else 0)
    offset = 14 + header_size + colors * 4 + (12 if compression == 3 and header_size == 40 else 0)
    bmp = b"BM" + (14 + len(dib)).to_bytes(4, "little") + b"\0\0\0\0" + offset.to_bytes(4, "little") + dib
    out = io.BytesIO()
    Image.open(io.BytesIO(bmp)).save(out, "PNG")
    return out.getvalue()


def _png_to_dib(png):
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "BMP")
    return out.getvalue()[14:]  # drop BITMAPFILEHEADER


def _secret():
    """True if the current content asks not to be recorded (clipboard open)."""
    if user32.IsClipboardFormatAvailable(CF_EXCLUDE):
        return True
    if user32.IsClipboardFormatAvailable(CF_HISTORY):
        value = _read_global(user32.GetClipboardData(CF_HISTORY))
        return bool(value) and int.from_bytes(value[:4], "little") == 0
    return False


class WindowsClipboard:
    last_secret = False  # whether the item last read by get() was marked secret

    def sequence(self):
        """Changes whenever anything writes the clipboard."""
        return user32.GetClipboardSequenceNumber()

    def get(self):
        """Current (mime, data), or None if it holds nothing we sync."""
        with _Open():
            self.last_secret = _secret()
            if user32.IsClipboardFormatAvailable(CF_PNG):
                data = _read_global(user32.GetClipboardData(CF_PNG))
                if data:
                    return PNG, data
            if user32.IsClipboardFormatAvailable(CF_DIB):
                dib = _read_global(user32.GetClipboardData(CF_DIB))
                if dib:
                    return PNG, _dib_to_png(dib)
            if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                raw = _read_global(user32.GetClipboardData(CF_UNICODETEXT))
                if raw is not None:
                    text = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
                    return TEXT, text.encode("utf-8")
        return None

    def set(self, mime, data, secret=False):
        with _Open():
            user32.EmptyClipboard()
            if secret:  # keep it out of Win+V history and cloud clipboard, like the app that copied it did
                user32.SetClipboardData(CF_EXCLUDE, _write_global(b"\0\0\0\0"))
                user32.SetClipboardData(CF_HISTORY, _write_global(b"\0\0\0\0"))
                user32.SetClipboardData(CF_CLOUD, _write_global(b"\0\0\0\0"))
            if mime == TEXT:
                text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\n", "\r\n")
                user32.SetClipboardData(CF_UNICODETEXT, _write_global((text + "\0").encode("utf-16-le")))
            elif mime == PNG:
                user32.SetClipboardData(CF_PNG, _write_global(data))
                user32.SetClipboardData(CF_DIB, _write_global(_png_to_dib(data)))
