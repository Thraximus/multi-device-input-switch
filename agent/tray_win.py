"""Windows notification-area icon (ctypes, no toolkit): shows who holds the devices; the menu switches.

Runs its own thread with a hidden window and message loop. Other threads only store new data and post
a message; everything Win32 happens on the tray thread.
"""
import ctypes
import logging
import os
import threading
from ctypes import wintypes

from . import tray

log = logging.getLogger(__name__)

LRESULT = wintypes.LPARAM
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY, WM_CLOSE, WM_NULL, WM_COMMAND = 0x0002, 0x0010, 0x0000, 0x0111
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1      # mouse events on the icon
WM_REFRESH = WM_APP + 2   # new status from the agent thread
WM_BALLOON = WM_APP + 3   # show/clear a notification (queued by the agent thread)
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
NIIF_NONE, NIIF_USER, NIIF_LARGE_ICON = 0, 0x4, 0x20
SM_CXICON, SM_CYICON = 11, 12
MF_STRING, MF_GRAYED, MF_CHECKED, MF_SEPARATOR, MFT_RADIOCHECK = 0, 1, 8, 0x800, 0x200
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CYSMICON = 49, 50


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sig = {
        (user32, "DefWindowProcW"): ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], LRESULT),
        (user32, "RegisterClassExW"): ([ctypes.POINTER(WNDCLASSEXW)], wintypes.ATOM),
        (user32, "CreateWindowExW"): ([wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID], wintypes.HWND),
        (user32, "DestroyWindow"): ([wintypes.HWND], wintypes.BOOL),
        (user32, "GetMessageW"): ([ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT],
                                  wintypes.BOOL),
        (user32, "TranslateMessage"): ([ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
        (user32, "DispatchMessageW"): ([ctypes.POINTER(wintypes.MSG)], LRESULT),
        (user32, "PostMessageW"): ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        (user32, "PostQuitMessage"): ([ctypes.c_int], None),
        (user32, "RegisterWindowMessageW"): ([wintypes.LPCWSTR], wintypes.UINT),
        (user32, "CreatePopupMenu"): ([], wintypes.HMENU),
        (user32, "AppendMenuW"): ([wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL),
        (user32, "DestroyMenu"): ([wintypes.HMENU], wintypes.BOOL),
        (user32, "TrackPopupMenu"): ([wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, wintypes.LPVOID], ctypes.c_int),
        (user32, "SetForegroundWindow"): ([wintypes.HWND], wintypes.BOOL),
        (user32, "GetCursorPos"): ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (user32, "LoadImageW"): ([wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT], wintypes.HANDLE),
        (user32, "DestroyIcon"): ([wintypes.HICON], wintypes.BOOL),
        (user32, "GetSystemMetrics"): ([ctypes.c_int], ctypes.c_int),
        (shell32, "Shell_NotifyIconW"): ([wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)], wintypes.BOOL),
        (kernel32, "GetModuleHandleW"): ([wintypes.LPCWSTR], wintypes.HMODULE),
    }
    for (dll, name), (args, res) in sig.items():
        fn = getattr(dll, name)
        fn.argtypes = args
        fn.restype = res
    return user32, shell32, kernel32


class WindowsTray:
    """on_switch(target_id_or_None) and on_quit() are called on the tray thread."""

    def __init__(self, icon_dir, log_file, version, on_switch, on_quit):
        self.icon_paths = tray.write_icons(icon_dir)
        self.log_file = log_file
        self.version = version
        self.on_switch = on_switch
        self.on_quit = on_quit
        self.status = None
        self.connected = False
        self.hwnd = None
        self._icons = {}
        self._added = False
        self._balloon_req = None
        self._balloon_icon = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)

    # --- called from any thread ----------------------------------------------

    def start(self):
        self._thread.start()
        self._ready.wait(5)

    def set_status(self, status):
        self.status = status
        self._post(WM_REFRESH)

    def set_connected(self, connected):
        self.connected = connected
        self._post(WM_REFRESH)

    def balloon(self, title, text, picture_png=None):
        """Show a notification from the tray icon (Windows 10 shows it as a toast). Thread-safe."""
        self._balloon_req = (title, text, picture_png)
        self._post(WM_BALLOON)

    def clear_balloon(self):
        self._balloon_req = None
        self._post(WM_BALLOON)

    def close(self):
        """Remove the icon now (before the process exits, or Windows leaves a ghost icon behind)."""
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._thread.join(2)

    def _post(self, msg):
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, msg, 0, 0)

    # --- tray thread ---------------------------------------------------------

    def _run(self):
        try:
            self.user32, self.shell32, kernel32 = _api()
            u = self.user32
            self._taskbar_created = u.RegisterWindowMessageW("TaskbarCreated")
            self._wndproc = WNDPROC(self._proc)  # keep a reference: Windows calls it
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc, hInstance=hinst,
                             lpszClassName="PiSwitcherAgentTray")
            u.RegisterClassExW(ctypes.byref(wc))
            # A normal hidden window (not message-only), so it receives TaskbarCreated after an Explorer restart.
            self.hwnd = u.CreateWindowExW(0, "PiSwitcherAgentTray", "pi-switcher agent", 0, 0, 0, 0, 0,
                                          None, None, hinst, None)
            cx, cy = u.GetSystemMetrics(SM_CXSMICON), u.GetSystemMetrics(SM_CYSMICON)
            for key, path in self.icon_paths.items():
                self._icons[key] = u.LoadImageW(None, str(path), IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
            self._notify(NIM_ADD)
        except Exception:
            log.exception("tray unavailable")
            self.hwnd = None
            return
        finally:
            self._ready.set()
        msg = wintypes.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))

    def _notify(self, op):
        nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1)
        if op != NIM_DELETE:
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self._icons.get(tray.icon_key(self.status, self.connected))
            nid.szTip = tray.tooltip(self.status, self.connected)
        ok = self.shell32.Shell_NotifyIconW(op, ctypes.byref(nid))
        if op == NIM_ADD:
            self._added = bool(ok)
        elif op == NIM_DELETE:
            self._added = False
        return ok

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY and lparam in (WM_LBUTTONUP, WM_RBUTTONUP):
                self._show_menu()
                return 0
            if msg == WM_REFRESH:
                self._notify(NIM_MODIFY if self._added else NIM_ADD)
                return 0
            if msg == WM_BALLOON:
                self._show_balloon(self._balloon_req)
                return 0
            if msg == self._taskbar_created:  # Explorer restarted: the icon is gone, add it again
                self._notify(NIM_ADD)
                return 0
            if msg == WM_CLOSE:
                self._notify(NIM_DELETE)
                self.user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self.user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("tray message %#x failed", msg)
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_balloon(self, req):
        nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1, uFlags=NIF_INFO)
        if req is not None:  # an empty text removes the notification on screen
            title, text, picture = req
            nid.szInfoTitle = (title or "")[:63]
            nid.szInfo = (text or " ")[:255]
            nid.dwInfoFlags = NIIF_NONE
            icon = self._picture_icon(picture) if picture else None
            if icon:
                nid.hBalloonIcon = icon
                nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _picture_icon(self, png):
        """The notification's picture as an icon (Pillow → .ico → LoadImage)."""
        try:
            import io
            from PIL import Image
            path = self.icon_paths[tray.HERE].parent / "balloon.ico"
            Image.open(io.BytesIO(png)).convert("RGBA").save(path, sizes=[(32, 32), (48, 48), (64, 64)])
            u = self.user32
            icon = u.LoadImageW(None, str(path), IMAGE_ICON, u.GetSystemMetrics(SM_CXICON) * 2,
                                u.GetSystemMetrics(SM_CYICON) * 2, LR_LOADFROMFILE)
            if self._balloon_icon:
                u.DestroyIcon(self._balloon_icon)
            self._balloon_icon = icon
            return icon
        except Exception:
            log.exception("notification picture failed")
            return None

    def _show_menu(self):
        u = self.user32
        items = tray.menu(self.status, self.connected, self.version)
        hmenu = u.CreatePopupMenu()
        for cmd, item in enumerate(items, start=1):
            if item.get("separator"):
                u.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                continue
            flags = MF_STRING
            if not item.get("enabled", True):
                flags |= MF_GRAYED
            if item.get("checked"):
                flags |= MF_CHECKED | MFT_RADIOCHECK
            u.AppendMenuW(hmenu, flags, cmd, item["label"])
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow(self.hwnd)  # so the menu closes when clicking elsewhere
        cmd = u.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self.hwnd, None)
        u.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        u.DestroyMenu(hmenu)
        if cmd <= 0:
            return
        item = items[cmd - 1]
        if "target" in item and not item.get("checked"):
            self.on_switch(item["target"])
        elif item.get("action") == "log":
            os.startfile(str(self.log_file))
        elif item.get("action") == "quit":
            self._notify(NIM_DELETE)
            self.on_quit()
