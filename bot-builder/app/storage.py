"""Пути к данным и простые операции с JSON-файлами."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"
TEMPLATES_DIR = DATA_DIR / "templates"
DATASET_IMAGES_DIR = DATA_DIR / "dataset" / "images"
DATASET_LABELS_DIR = DATA_DIR / "dataset" / "labels"
MODELS_DIR = DATA_DIR / "models"


def ensure_dirs() -> None:
    for d in (SCENARIOS_DIR, TEMPLATES_DIR, DATASET_IMAGES_DIR, DATASET_LABELS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def safe_name(name: str) -> str:
    """Превращает пользовательский текст в безопасное имя файла (буквы, цифры, дефис)."""
    name = (name or "").strip()
    name = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name)
    return name[:60]


def new_id(prefix: str) -> str:
    return time.strftime(f"{prefix}_%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
