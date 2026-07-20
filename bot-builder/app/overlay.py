"""HUD-окошко поверх игры: полупрозрачное, всегда сверху, клики проходят сквозь него.

Работает через tkinter (встроен в Python). Если дисплея нет (сервер без экрана),
HUD просто недоступен — тогда он виден только в браузере.
"""
from __future__ import annotations

import queue
import sys
import threading

_cmd: "queue.Queue[tuple]" = queue.Queue()
_started = False
_ok = False
_lock = threading.Lock()


def set_lines(lines: list[str]) -> bool:
    """Показывает строки на HUD (пустой список — спрятать). Возвращает False, если HUD недоступен."""
    if not _ensure_started():
        return False
    _cmd.put(("set", list(lines)))
    return True


def _ensure_started() -> bool:
    global _started, _ok
    with _lock:
        if _started:
            return _ok
        _started = True
        ready = threading.Event()
        threading.Thread(target=_run, args=(ready,), daemon=True).start()
        ready.wait(timeout=5)
        return _ok


def _run(ready: threading.Event) -> None:
    global _ok
    try:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)          # без рамки и заголовка
        root.attributes("-topmost", True)    # поверх всех окон
        try:
            root.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        label = tk.Label(root, text="", justify="left", anchor="nw",
                         font=("Consolas", 13, "bold"),
                         bg="#101418", fg="#7CFC9A", padx=12, pady=8)
        label.pack()
        sw = root.winfo_screenwidth()
        root.geometry(f"+{sw - 340}+24")     # правый верхний угол
        root.withdraw()

        if sys.platform == "win32":
            _click_through(root)

        def poll():
            try:
                while True:
                    cmd, arg = _cmd.get_nowait()
                    if cmd == "set":
                        if arg:
                            label.config(text="\n".join(arg))
                            root.deiconify()
                            root.attributes("-topmost", True)
                            root.geometry(f"+{sw - max(340, label.winfo_reqwidth() + 20)}+24")
                        else:
                            root.withdraw()
            except queue.Empty:
                pass
            root.after(200, poll)

        _ok = True
        ready.set()
        poll()
        root.mainloop()
    except Exception:
        _ok = False
        ready.set()


def _click_through(root) -> None:
    """Windows: делаем окно «прозрачным» для кликов, чтобы не мешать игре."""
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                            style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
    except Exception:
        pass
