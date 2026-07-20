"""Нейросеть-детектор: обучение на размеченных снимках и поиск объектов на экране.

Как устроено: маленькая свёрточная сеть учится отличать кусочки экрана 64×64
(«патчи») — фон это или один из твоих объектов. Сеть полностью свёрточная,
поэтому на целом снимке она за один проход выдаёт карту вероятностей для всех
позиций сразу (шаг сетки 16 пикселей). Чтобы находить объекты разного размера,
снимок проверяется в нескольких масштабах, потом совпадения чистятся NMS.
"""
from __future__ import annotations

import importlib.util
import math
import random
import threading
import time
from typing import Callable, Optional

try:
    import numpy as np
except ImportError:
    np = None
try:
    import cv2
except ImportError:
    cv2 = None

from . import storage
from .vision import VisionError

PATCH = 64    # размер патча, на котором учится сеть
STRIDE = 16   # шаг карты вероятностей (2^4 из-за четырёх pool-слоёв)


def torch_available() -> bool:
    return importlib.util.find_spec("torch") is not None


def _torch():
    if not torch_available():
        raise VisionError(
            "PyTorch не установлен — обучение и нейропоиск недоступны. Выполни на ПК: pip install torch"
        )
    import torch
    return torch


def _build_net(torch, num_classes: int):
    nn = torch.nn
    return nn.Sequential(
        nn.Conv2d(3, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(16, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(32, 48, 3, padding=1), nn.BatchNorm2d(48), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(48, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(64, 96, 4), nn.ReLU(),        # 4×4 → 1×1 на патче 64
        nn.Dropout2d(0.15),
        nn.Conv2d(96, num_classes + 1, 1),      # +1 — класс «фон»
    )


# ---------------------------------------------------------------- датасет

def dataset_items() -> list[dict]:
    items = []
    for img_path in sorted(storage.DATASET_IMAGES_DIR.glob("*.png")):
        label = storage.load_json(storage.DATASET_LABELS_DIR / f"{img_path.stem}.json", {}) or {}
        items.append({"id": img_path.stem, "path": img_path, "boxes": label.get("boxes", [])})
    return items


def _crop_square(img, cx: float, cy: float, side: float):
    """Квадратный вырез вокруг точки с добивкой краёв, приведённый к PATCH×PATCH."""
    h, w = img.shape[:2]
    half = side / 2
    x0, y0 = int(round(cx - half)), int(round(cy - half))
    x1, y1 = x0 + int(round(side)), y0 + int(round(side))
    crop = img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    if crop.size == 0:
        return None
    pad_l, pad_t = max(0, -x0), max(0, -y0)
    pad_r, pad_b = max(0, x1 - w), max(0, y1 - h)
    if pad_l or pad_t or pad_r or pad_b:
        crop = cv2.copyMakeBorder(crop, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_REPLICATE)
    return cv2.resize(crop, (PATCH, PATCH), interpolation=cv2.INTER_AREA)


def _augment(patch, rng: random.Random):
    """Случайные искажения, чтобы сеть не запоминала примеры наизусть."""
    out = patch
    if rng.random() < 0.5:
        out = out[:, ::-1]
    if rng.random() < 0.2:
        out = out[::-1, :]
    alpha, beta = rng.uniform(0.8, 1.2), rng.uniform(-18, 18)
    out = np.clip(out.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(out)


def _hits_box(cx: float, cy: float, side: float, b: dict) -> bool:
    """True, если квадрат задевает размеченную рамку (такой кусок нельзя брать как фон)."""
    ax0, ay0, ax1, ay1 = cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2
    bx0, by0, bx1, by1 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    if inter <= 0:
        return False
    box_area = b["w"] * b["h"]
    union = side * side + box_area - inter
    return inter / union > 0.15 or inter > 0.5 * box_area


def _build_samples(items, classes, rng: random.Random):
    """Собирает обучающие примеры: вырезки объектов (с искажениями) и куски фона."""
    pos, neg = [], []
    total_boxes = sum(len(it["boxes"]) for it in items)
    per_box = min(80, max(12, math.ceil(600 / max(1, total_boxes))))
    for it in items:
        if not it["boxes"]:
            # неразмеченный снимок: на нём могут быть объекты, поэтому
            # брать из него «фон» нельзя — пропускаем целиком
            continue
        img = cv2.imread(str(it["path"]))
        if img is None:
            continue
        h, w = img.shape[:2]
        for b in it["boxes"]:
            side0 = max(b["w"], b["h"])
            if side0 < 8 or b.get("cls") not in classes:
                continue
            cls_idx = classes.index(b["cls"]) + 1
            for _ in range(per_box):
                side = side0 * rng.uniform(0.85, 1.3)
                cx = b["x"] + b["w"] / 2 + rng.uniform(-0.12, 0.12) * side
                cy = b["y"] + b["h"] / 2 + rng.uniform(-0.12, 0.12) * side
                p = _crop_square(img, cx, cy, side)
                if p is not None:
                    pos.append((_augment(p, rng), cls_idx))
        want = int(per_box * max(1, len(it["boxes"])) * 1.5)
        got = tries = 0
        while got < want and tries < want * 20:
            tries += 1
            side = rng.uniform(40, 110)
            cx, cy = rng.uniform(0, w), rng.uniform(0, h)
            if any(_hits_box(cx, cy, side, b) for b in it["boxes"]):
                continue
            p = _crop_square(img, cx, cy, side)
            if p is not None:
                neg.append((_augment(p, rng), 0))
                got += 1
    return pos, neg


# ---------------------------------------------------------------- обучение

_train_state = {
    "running": False, "model": None, "epoch": 0, "epochs": 0,
    "loss": None, "acc": None, "error": None, "done": False,
}
_train_lock = threading.Lock()


def train_status() -> dict:
    return dict(_train_state)


def start_training(name: str, epochs: int, log: Callable) -> None:
    _torch()
    if cv2 is None or np is None:
        raise VisionError("Нужны numpy и opencv-python: pip install numpy opencv-python")
    with _train_lock:
        if _train_state["running"]:
            raise VisionError("Обучение уже идёт — дождись окончания")
        items = dataset_items()
        classes = sorted({b.get("cls", "") for it in items for b in it["boxes"]} - {""})
        n_boxes = sum(len(it["boxes"]) for it in items)
        if not classes or n_boxes < 5:
            raise VisionError("Мало данных: обведи рамками хотя бы 5 объектов (лучше 30–50)")
        _train_state.update(running=True, model=name, epoch=0, epochs=epochs,
                            loss=None, acc=None, error=None, done=False)
    threading.Thread(target=_train_worker, args=(name, epochs, items, classes, n_boxes, log),
                     daemon=True).start()


def _train_worker(name, epochs, items, classes, n_boxes, log):
    try:
        torch = _torch()
        rng = random.Random(42)
        log(f"🧠 Обучение «{name}»: собираю примеры из {len(items)} снимков ({n_boxes} рамок)…")
        pos, neg = _build_samples(items, classes, rng)
        if len(pos) < 50:
            raise VisionError("Слишком мало примеров — добавь снимков и рамок")
        samples = pos + neg
        rng.shuffle(samples)
        X = np.stack([s[0] for s in samples]).astype(np.float32)
        X = np.ascontiguousarray(X.transpose(0, 3, 1, 2))  # NHWC → NCHW, каналы BGR
        X = (X / 255.0 - 0.5) / 0.25
        y = np.array([s[1] for s in samples], dtype=np.int64)

        n_val = max(1, int(len(X) * 0.15))
        Xt, yt = torch.from_numpy(X[:-n_val]), torch.from_numpy(y[:-n_val])
        Xv, yv = torch.from_numpy(X[-n_val:]), torch.from_numpy(y[-n_val:])
        device = "cuda" if torch.cuda.is_available() else "cpu"
        net = _build_net(torch, len(classes)).to(device)
        opt = torch.optim.Adam(net.parameters(), lr=1e-3)
        loss_fn = torch.nn.CrossEntropyLoss()
        log(f"Примеров: {len(pos)} с объектами, {len(neg)} с фоном. Устройство: {device}. Эпох: {epochs}")

        best_acc, best_state = 0.0, None
        for ep in range(1, epochs + 1):
            net.train()
            perm = torch.randperm(len(Xt))
            total_loss = 0.0
            for i in range(0, len(Xt), 64):
                idx = perm[i:i + 64]
                if len(idx) < 2:  # BatchNorm не умеет батч из одного примера
                    continue
                xb, yb = Xt[idx].to(device), yt[idx].to(device)
                out = net(xb).flatten(1)
                loss = loss_fn(out, yb)
                opt.zero_grad()
                loss.backward()
                opt.step()
                total_loss += loss.item() * len(idx)
            net.eval()
            correct = 0
            with torch.no_grad():
                for i in range(0, len(Xv), 256):
                    pred = net(Xv[i:i + 256].to(device)).flatten(1).argmax(1).cpu()
                    correct += int((pred == yv[i:i + 256]).sum())
            acc = correct / len(Xv)
            if acc >= best_acc:
                best_acc = acc
                best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
            avg_loss = total_loss / max(1, len(Xt))
            _train_state.update(epoch=ep, loss=round(avg_loss, 4), acc=round(acc, 3))
            log(f"Эпоха {ep}/{epochs}: ошибка {avg_loss:.4f}, точность {acc * 100:.1f}%")

        fname = storage.safe_name(name) or "model"
        torch.save({"state": best_state, "classes": classes, "patch": PATCH},
                   storage.MODELS_DIR / f"{fname}.pt")
        storage.save_json(storage.MODELS_DIR / f"{fname}.json", {
            "name": fname, "classes": classes, "accuracy": round(best_acc, 3),
            "images": len(items), "boxes": n_boxes,
            "trained_at": time.strftime("%Y-%m-%d %H:%M"),
        })
        _cache.pop(fname, None)
        log(f"✅ Модель «{fname}» готова (точность {best_acc * 100:.1f}%). "
            f"Выбери её в блоке «Найти объект (нейросеть)».")
        _train_state["done"] = True
    except Exception as e:  # noqa: BLE001 — любую ошибку показываем в журнале
        _train_state["error"] = str(e)
        log(f"❌ Ошибка обучения: {e}", "error")
    finally:
        _train_state["running"] = False


# ---------------------------------------------------------------- модели и поиск

_cache: dict[str, tuple] = {}


def list_models() -> list[dict]:
    out = []
    for p in sorted(storage.MODELS_DIR.glob("*.json")):
        meta = storage.load_json(p)
        if meta and (storage.MODELS_DIR / f"{p.stem}.pt").exists():
            out.append(meta)
    return out


def delete_model(name: str) -> None:
    for ext in (".pt", ".json"):
        p = storage.MODELS_DIR / f"{name}{ext}"
        if p.exists():
            p.unlink()
    _cache.pop(name, None)


def _load(name: str):
    torch = _torch()
    path = storage.MODELS_DIR / f"{name}.pt"
    if not path.exists():
        raise VisionError(f"Модель «{name}» не найдена — сначала обучи её на вкладке «Обучение»")
    mtime = path.stat().st_mtime
    hit = _cache.get(name)
    if hit and hit[2] == mtime:
        return hit[0], hit[1]
    data = torch.load(path, map_location="cpu")
    net = _build_net(torch, len(data["classes"]))
    net.load_state_dict(data["state"])
    net.eval()
    _cache[name] = (net, data["classes"], mtime)
    return net, data["classes"]


def _nms(boxes: list[dict], radius_k: float = 0.75) -> list[dict]:
    """Убирает дубли: сетка даёт несколько окон вокруг одного объекта,
    оставляем самое уверенное, а окна того же класса с центром ближе
    radius_k·(средняя ширина) считаем повторами."""
    boxes = sorted(boxes, key=lambda b: -b["score"])[:400]
    keep = []
    for b in boxes:
        bcx, bcy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        dup = False
        for k in keep:
            if k["cls"] != b["cls"]:
                continue
            kcx, kcy = k["x"] + k["w"] / 2, k["y"] + k["h"] / 2
            thr = radius_k * (k["w"] + b["w"]) / 2
            if (bcx - kcx) ** 2 + (bcy - kcy) ** 2 < thr * thr:
                dup = True
                break
        if not dup:
            keep.append(b)
    return keep


def detect(screen, model_name: str, class_name: Optional[str] = None,
           conf: float = 0.6, max_side: int = 2600) -> list[dict]:
    """Ищет объекты на снимке. Возвращает рамки [{x, y, w, h, score, cls}] по убыванию score."""
    torch = _torch()
    net, classes = _load(model_name)
    if class_name and class_name not in classes:
        raise VisionError(
            f"В модели «{model_name}» нет класса «{class_name}» (есть: {', '.join(classes)})"
        )
    h0, w0 = screen.shape[:2]
    pre = 1.0
    if max(h0, w0) > 1600:  # большие экраны ужимаем ради скорости и памяти
        pre = 1600 / max(h0, w0)
        screen = cv2.resize(screen, (int(w0 * pre), int(h0 * pre)), interpolation=cv2.INTER_AREA)

    boxes = []
    with torch.no_grad():
        for s in (0.6, 1.0, 1.5):
            img = screen if s == 1.0 else cv2.resize(
                screen, None, fx=s, fy=s,
                interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            ih, iw = img.shape[:2]
            if min(ih, iw) < PATCH or max(ih, iw) > max_side:
                continue
            x = torch.from_numpy(np.ascontiguousarray(img.transpose(2, 0, 1), dtype=np.float32))
            x = (x / 255.0 - 0.5) / 0.25
            prob = torch.softmax(net(x.unsqueeze(0))[0], dim=0).numpy()  # (классы+1, gh, gw)
            total = s * pre
            for ci, cname in enumerate(classes, start=1):
                if class_name and cname != class_name:
                    continue
                ys, xs = np.where(prob[ci] >= conf)
                for gy, gx in zip(ys, xs):
                    boxes.append({
                        "x": int(gx * STRIDE / total), "y": int(gy * STRIDE / total),
                        "w": int(PATCH / total), "h": int(PATCH / total),
                        "score": round(float(prob[ci, gy, gx]), 3), "cls": cname,
                    })
    return _nms(boxes)[:60]
