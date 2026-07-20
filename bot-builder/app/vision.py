"""Работа с экраном: снимки, поиск по картинке-образцу, кодирование изображений."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None
try:
    import cv2
except ImportError:
    cv2 = None
try:
    import mss
except Exception:
    mss = None

from . import storage


class VisionError(RuntimeError):
    """Понятная пользователю ошибка (показывается в журнале и в интерфейсе)."""


def _need(module, name: str, pip_name: str) -> None:
    if module is None:
        raise VisionError(f"Модуль {name} не установлен. Выполни на ПК: pip install {pip_name}")


def capture_screen():
    """Снимок основного монитора. Возвращает (BGR-картинка, (left, top) монитора)."""
    _need(mss, "mss", "mss")
    _need(np, "numpy", "numpy")
    with mss.mss() as sct:
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        raw = np.array(sct.grab(mon))  # BGRA
        return raw[:, :, :3].copy(), (mon["left"], mon["top"])


def windows_available() -> bool:
    try:
        import pygetwindow  # noqa: F401
        return True
    except Exception:
        return False


def list_windows() -> list[str]:
    """Заголовки открытых окон — для привязки бота к окну игры."""
    try:
        import pygetwindow as gw
    except Exception:
        return []
    try:
        titles = {t.strip() for t in gw.getAllTitles() if t and t.strip()}
    except Exception:
        return []
    return sorted(titles)[:100]


def capture_window(title_part: str):
    """Снимок конкретного окна по части заголовка. Возвращает (BGR, (left, top))."""
    try:
        import pygetwindow as gw
    except Exception:
        raise VisionError(
            "Для привязки к окну установи pygetwindow: pip install pygetwindow (работает на Windows)"
        )
    _need(mss, "mss", "mss")
    _need(np, "numpy", "numpy")
    part = title_part.lower()
    wins = [w for w in gw.getAllWindows()
            if part in (w.title or "").lower() and w.width > 50 and w.height > 50]
    if not wins:
        raise VisionError(f"Окно с «{title_part}» в заголовке не найдено — проверь, что игра запущена")
    w = wins[0]
    if getattr(w, "isMinimized", False):
        raise VisionError(f"Окно «{w.title}» свёрнуто — разверни его")
    box = {"left": int(w.left), "top": int(w.top), "width": int(w.width), "height": int(w.height)}
    with mss.mss() as sct:
        raw = np.array(sct.grab(box))
    return raw[:, :, :3].copy(), (box["left"], box["top"])


def draw_overlay(img, origin, overlay, now: float) -> None:
    """«Глаза бота»: рисует на живом просмотре найденные цели и точки кликов."""
    if cv2 is None or not overlay:
        return
    ox, oy = origin
    if now - overlay.get("t", 0) < 4:
        for x, y, w, h in overlay.get("boxes", [])[:60]:
            cv2.rectangle(img, (int(x - ox), int(y - oy)),
                          (int(x - ox + w), int(y - oy + h)), (120, 220, 60), 2)
    for x, y, t0 in list(overlay.get("clicks", [])):
        age = now - t0
        if age < 2.5:
            r = int(10 + age * 14)
            cv2.circle(img, (int(x - ox), int(y - oy)), r, (60, 60, 230), 2)


def encode_jpeg(img, quality: int = 75) -> bytes:
    _need(cv2, "opencv", "opencv-python")
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise VisionError("Не удалось закодировать JPEG")
    return buf.tobytes()


def resize_width(img, width: int):
    """Ужимает картинку до заданной ширины (если она шире)."""
    _need(cv2, "opencv", "opencv-python")
    h, w = img.shape[:2]
    if w <= width:
        return img
    return cv2.resize(img, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)


def save_png(path: Path, img) -> None:
    _need(cv2, "opencv", "opencv-python")
    if not cv2.imwrite(str(path), img):
        raise VisionError(f"Не удалось сохранить {Path(path).name}")


def read_image(path: Path):
    _need(cv2, "opencv", "opencv-python")
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise VisionError(f"Не удалось прочитать {Path(path).name}")
    return img


def load_template(name: str):
    path = storage.TEMPLATES_DIR / f"{name}.png"
    if not path.exists():
        raise VisionError(f"Картинка-образец «{name}» не найдена — сохрани её на вкладке «Экран»")
    return read_image(path)


def _match_map(screen, template):
    """Карта совпадений образца по снимку: чем ближе к 1, тем лучше совпадение."""
    per_channel_std = template.reshape(-1, template.shape[2] if template.ndim == 3 else 1).std(axis=0)
    if float(per_channel_std.max()) < 2.0:
        # почти однотонный образец: у CCOEFF_NORMED деление на ноль и ложные
        # совпадения, поэтому сравниваем разностью
        return 1.0 - cv2.matchTemplate(screen, template, cv2.TM_SQDIFF_NORMED)
    return cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)


def find_template(screen, template, threshold: float) -> Optional[dict]:
    """Лучшее совпадение образца на снимке или None, если совпадение хуже порога."""
    hits = find_template_all(screen, template, threshold, max_results=1)
    return hits[0] if hits else None


def find_template_all(screen, template, threshold: float, max_results: int = 50) -> list[dict]:
    """Все совпадения образца (лучшие первыми). Повторы рядом подавляются."""
    _need(cv2, "opencv", "opencv-python")
    th, tw = template.shape[:2]
    sh, sw = screen.shape[:2]
    if th > sh or tw > sw:
        return []
    res = _match_map(screen, template)
    hits = []
    for _ in range(max_results):
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < threshold:
            break
        x, y = int(max_loc[0]), int(max_loc[1])
        hits.append({"x": x, "y": y, "w": int(tw), "h": int(th),
                     "score": round(float(max_val), 3)})
        # гасим окрестность найденного, чтобы не находить его же снова
        res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1.0
    return hits
