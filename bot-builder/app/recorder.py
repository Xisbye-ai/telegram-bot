"""Запись действий пользователя (мышь, клавиатура) и превращение их в блоки сценария."""
from __future__ import annotations

import threading
import time

from .vision import VisionError

# модификаторы pynput → наши имена
_MODS = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win",
}
_MOD_ORDER = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}


def available() -> bool:
    try:
        import pynput  # noqa: F401
        return True
    except Exception:
        return False


class Recorder:
    """Слушает глобальные клики и клавиши, копит события с отметками времени."""

    def __init__(self):
        self._mouse = None
        self._kb = None
        self._lock = threading.Lock()
        self._mods: set[str] = set()
        self.events: list[dict] = []
        self.recording = False
        self.ignore_keys: set[str] = set()

    def start(self, ignore_keys: set[str] | None = None) -> None:
        if self.recording:
            return
        if not available():
            raise VisionError("Для записи действий нужен pynput: pip install pynput")
        from pynput import keyboard, mouse

        self.events = []
        self._mods = set()
        self.ignore_keys = {k.lower() for k in (ignore_keys or set())}
        self.recording = True
        self._mouse = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self._kb = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._mouse.daemon = self._kb.daemon = True
        self._mouse.start()
        self._kb.start()

    def stop(self) -> list[dict]:
        self.recording = False
        for lst in (self._mouse, self._kb):
            try:
                if lst:
                    lst.stop()
            except Exception:
                pass
        self._mouse = self._kb = None
        with self._lock:
            return list(self.events)

    def count(self) -> int:
        with self._lock:
            return len(self.events)

    def _add(self, ev: dict) -> None:
        ev["t"] = time.time()
        with self._lock:
            self.events.append(ev)

    # ------------------------------------------------------------ мышь

    def _on_click(self, x, y, button, pressed):
        if not (self.recording and pressed):
            return
        name = getattr(button, "name", "left")
        if name not in ("left", "right"):
            name = "left"
        self._add({"type": "click", "x": int(x), "y": int(y), "button": name})

    def _on_scroll(self, x, y, dx, dy):
        if not self.recording or not dy:
            return
        self._add({"type": "scroll", "dy": int(dy)})

    # ------------------------------------------------------------ клавиатура

    @staticmethod
    def _key_name(key) -> str:
        ch = getattr(key, "char", None)
        if ch:
            return ch
        return getattr(key, "name", "") or ""

    def _on_press(self, key):
        if not self.recording:
            return
        name = self._key_name(key)
        mod = _MODS.get(name.lower())
        if mod:
            self._mods.add(mod)
            return
        if name.lower() in self.ignore_keys:
            return
        hard_mods = self._mods - {"shift"}  # shift+буква — это просто заглавная буква
        if hard_mods:
            combo = "+".join(sorted(self._mods, key=lambda m: _MOD_ORDER[m]))
            self._add({"type": "key", "combo": f"{combo}+{name.lower()}"})
        elif len(name) == 1:
            self._add({"type": "char", "ch": name})
        else:
            self._add({"type": "key", "combo": name.lower()})

    def _on_release(self, key):
        mod = _MODS.get(self._key_name(key).lower())
        if mod:
            self._mods.discard(mod)


# ---------------------------------------------------------------- события → блоки

def events_to_blocks(events: list[dict], min_pause: float = 0.35,
                     double_dt: float = 0.4, double_dist: int = 6) -> list[dict]:
    """Превращает записанные события в блоки: клики, двойные клики,
    набор текста, клавиши, прокрутку и паузы между ними."""
    blocks: list[dict] = []
    prev_t: float | None = None
    i = 0
    n = len(events)

    def pause_before(t: float) -> None:
        nonlocal prev_t
        if prev_t is not None:
            gap = t - prev_t
            if gap >= min_pause:
                blocks.append({"type": "wait", "params": {"seconds": round(gap, 1), "rand": 0}})

    while i < n:
        ev = events[i]
        pause_before(ev["t"])
        if ev["type"] == "click":
            nxt = events[i + 1] if i + 1 < n else None
            if (nxt and nxt["type"] == "click" and nxt["button"] == ev["button"]
                    and nxt["t"] - ev["t"] <= double_dt
                    and abs(nxt["x"] - ev["x"]) <= double_dist
                    and abs(nxt["y"] - ev["y"]) <= double_dist):
                btn = "double" if ev["button"] == "left" else "right"
                prev_t = nxt["t"]
                i += 2
            else:
                btn = ev["button"]
                prev_t = ev["t"]
                i += 1
            blocks.append({"type": "click", "params": {
                "target": "coords", "x": ev["x"], "y": ev["y"],
                "button": btn, "jitter": 0,
            }})
        elif ev["type"] == "scroll":
            total = 0
            last = ev["t"]
            while i < n and events[i]["type"] == "scroll" and events[i]["t"] - last <= 0.6:
                total += events[i]["dy"]
                last = events[i]["t"]
                i += 1
            prev_t = last
            if total:
                # pynput считает «щелчками» колеса, наш блок — в сырых единицах (~120 на щелчок)
                blocks.append({"type": "scroll", "params": {"amount": total * 120}})
        elif ev["type"] == "char":
            text = ""
            last = ev["t"]
            while i < n and events[i]["type"] == "char" and events[i]["t"] - last <= 1.2:
                text += events[i]["ch"]
                last = events[i]["t"]
                i += 1
            prev_t = last
            blocks.append({"type": "type_text", "params": {"text": text}})
        elif ev["type"] == "key":
            blocks.append({"type": "press_key", "params": {"keys": ev["combo"]}})
            prev_t = ev["t"]
            i += 1
        else:
            prev_t = ev["t"]
            i += 1
    return blocks


recorder = Recorder()
