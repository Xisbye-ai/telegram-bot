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


def find_template(screen, template, threshold: float) -> Optional[dict]:
    """Лучшее совпадение образца на снимке или None, если совпадение хуже порога."""
    _need(cv2, "opencv", "opencv-python")
    th, tw = template.shape[:2]
    sh, sw = screen.shape[:2]
    if th > sh or tw > sw:
        return None
    per_channel_std = template.reshape(-1, template.shape[2] if template.ndim == 3 else 1).std(axis=0)
    if float(per_channel_std.max()) < 2.0:
        # почти однотонный образец: у CCOEFF_NORMED деление на ноль и ложные
        # совпадения, поэтому сравниваем разностью
        res = cv2.matchTemplate(screen, template, cv2.TM_SQDIFF_NORMED)
        min_val, _, min_loc, _ = cv2.minMaxLoc(res)
        score, loc = 1.0 - float(min_val), min_loc
    else:
        res = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        score, loc = float(max_val), max_loc
    if score < threshold:
        return None
    return {
        "x": int(loc[0]),
        "y": int(loc[1]),
        "w": int(tw),
        "h": int(th),
        "score": round(score, 3),
    }
