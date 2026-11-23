"""Opens / focuses the CommiSense assistant window from inside the backend (Windows only).
Runs as a background task of the main app - no separate notifier process needed."""
import os, sys, time, asyncio, subprocess
from datetime import datetime, timedelta

API = os.getenv("COMMIQ_API", "http://127.0.0.1:8000")
PROFILE = os.path.join(os.getenv("LOCALAPPDATA", os.path.expanduser("~")), "CommiSense", "popup-profile")


def _browser():
    c = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
         r"C:\Program Files\Google\Chrome\Application\chrome.exe",
         r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
         os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]
    return next((p for p in c if os.path.exists(p)), None)


WIN_W, WIN_H, MARGIN = 340, 500, 16          # initial window size and gap from the screen corner


def _find_window():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if "CommiSense Assistant" in buf.value and u.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else None


def _place_window(h):
    """Pin the freshly opened window to the top-right at its initial size. The user can still resize it."""
    import ctypes
    u = ctypes.windll.user32
    x, y = max(0, u.GetSystemMetrics(0) - WIN_W - MARGIN), MARGIN
    u.SetWindowPos(h, 0, x, y, WIN_W, WIN_H, 0x0004)              # NOZORDER


def _place_when_ready():
    for _ in range(40):                                           # wait up to ~10 s for the window to appear
        h = _find_window()
        if h:
            _place_window(h)
            return
        time.sleep(0.25)


def focus_popup() -> bool:
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if "CommiSense Assistant" in buf.value:
                found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    if not found:
        return False
    h = found[0]
    u.ShowWindow(h, 9)                                   # restore if minimised
    u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)
    u.SetWindowPos(h, -1, 0, 0, 0, 0, 0x0003)            # topmost, then back to normal
    u.SetWindowPos(h, -2, 0, 0, 0, 0, 0x0003)
    u.SetForegroundWindow(h)
    return True


def launch_popup(exe):
    import threading
    import ctypes
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    os.makedirs(PROFILE, exist_ok=True)
    subprocess.Popen([exe, f"--app={API}/assistant/ui", f"--window-size={WIN_W},{WIN_H}",
                      f"--window-position={max(0, sw - WIN_W - MARGIN)},{MARGIN}",
                      f"--user-data-dir={PROFILE}", "--no-first-run"])
    threading.Thread(target=_place_when_ready, daemon=True).start()
    print("Assistant window opened (top-right, resizable)")


def _pending_ids():
    from app.database import SessionLocal
    from app.notifications.models import Notification
    db = SessionLocal()
    try:
        rows = db.query(Notification.id).filter(Notification.status == "pending",
                                                Notification.deliver_after <= datetime.now()).all()
        return {r[0] for r in rows}
    finally:
        db.close()


def _startup_briefs():
    """Daily brief every time the app starts (throttled to once per 10 min); weekly on Mondays."""
    from app.database import SessionLocal
    from app.notifications.models import Notification
    from app.briefs.service import announce
    db = SessionLocal()
    try:
        last = (db.query(Notification).filter(Notification.dedupe_key.like("brief:daily:%"))
                .order_by(Notification.id.desc()).first())
        if not last or datetime.now() - (last.created_at or datetime.min) > timedelta(minutes=10):
            announce(db, "daily")
        if datetime.now().weekday() == 0:
            announce(db, "weekly")
    except Exception as e:
        print(f"Startup brief skipped: {e}")
    finally:
        db.close()


async def popup_loop():
    if sys.platform != "win32":
        return
    exe = _browser()
    if not exe:
        print("Assistant popup disabled: Edge/Chrome not found")
        return
    from app.notifications import chat
    await asyncio.sleep(4)
    await asyncio.to_thread(_startup_briefs)
    last_seq, last_launch, last_check, seen = chat._main["seq"], 0.0, 0.0, set()

    async def show(is_open):
        nonlocal last_launch
        if is_open and await asyncio.to_thread(focus_popup):
            return
        if time.time() - last_launch > 8:
            last_launch = time.time()
            await asyncio.to_thread(launch_popup, exe)

    while True:
        try:
            is_open = time.time() - chat._beat[0] < 12
            if chat._main["seq"] != last_seq:                 # dashboard minimised / hidden
                last_seq = chat._main["seq"]
                if chat._main["hidden"]:
                    await show(is_open)
            if time.time() - last_check > 4:                  # new alerts waiting
                last_check = time.time()
                ids = await asyncio.to_thread(_pending_ids)
                fresh = ids - seen
                seen |= ids
                if fresh:
                    await show(is_open)
        except Exception as e:
            print(f"Popup loop error: {e}")
        await asyncio.sleep(2)