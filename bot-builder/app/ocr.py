"""Чтение текста с экрана (OCR) через Tesseract.

Tesseract — отдельная программа, её нужно поставить один раз:
Windows: https://github.com/UB-Mannheim/tesseract/wiki (при установке отметь
русский язык), Linux: sudo apt install tesseract-ocr tesseract-ocr-rus.
"""
from __future__ import annotations

import importlib.util
import shutil

try:
    import cv2
except ImportError:
    cv2 = None

from .vision import VisionError

_INSTALL_HINT = (
    "Чтение текста недоступно: нужен Tesseract. Выполни: pip install pytesseract, "
    "затем установи программу Tesseract (см. README, раздел «Чтение текста»)"
)


def available() -> bool:
    if importlib.util.find_spec("pytesseract") is None:
        return False
    return shutil.which("tesseract") is not None


def read_text(img_bgr, digits: bool = False) -> str:
    """Распознаёт текст на кусочке снимка. digits=True — только числа."""
    if not available():
        raise VisionError(_INSTALL_HINT)
    if cv2 is None:
        raise VisionError("Нужен opencv-python: pip install opencv-python")
    import pytesseract

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h = gray.shape[0]
    if h < 40:  # мелкий текст OCR читает плохо — увеличиваем
        k = max(2, 40 // max(1, h) + 1)
        gray = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)

    # одна строка текста — psm 7, блок текста — psm 6
    config = "--psm 7" if h < 60 else "--psm 6"
    if digits:
        config += " -c tessedit_char_whitelist=0123456789.,-+"
        lang = "eng"
    else:
        lang = "rus+eng"

    def run(image, language):
        try:
            return pytesseract.image_to_string(image, lang=language, config=config).strip()
        except pytesseract.TesseractError:
            if language != "eng":  # русский языковой пакет не установлен
                return pytesseract.image_to_string(image, lang="eng", config=config).strip()
            raise

    text = run(gray, lang)
    if not text:  # светлый текст на тёмном фоне читается лучше в негативе
        text = run(255 - gray, lang)
    return text.replace("\n", " ").strip()
