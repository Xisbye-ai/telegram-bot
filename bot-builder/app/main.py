"""Веб-сервер: отдаёт интерфейс конструктора и API для него."""
from __future__ import annotations

import asyncio
import base64
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import detector, engine, storage, vision
from .vision import VisionError

storage.ensure_dirs()
STATIC_DIR = Path(__file__).parent / "static"


class LogHub:
    """Журнал: копит строки и рассылает их в открытые вкладки по WebSocket."""

    def __init__(self):
        self.history: deque = deque(maxlen=400)
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def log(self, msg: str, level: str = "info") -> None:
        item = {"time": time.strftime("%H:%M:%S"), "msg": str(msg), "level": level}
        self.history.append(item)
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._broadcast, item)

    def _broadcast(self, item: dict) -> None:
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, item))

    async def _send(self, ws: WebSocket, item: dict) -> None:
        try:
            await ws.send_json(item)
        except Exception:
            self.clients.discard(ws)


hub = LogHub()
bot = engine.BotEngine(hub.log)

# «замороженный» снимок экрана: из него вырезаются образцы и снимки для обучения
_frame_lock = threading.Lock()
_frozen: dict = {"img": None}


def _start_stop_hotkey():
    """Глобальная клавиша F10 — остановить бота, даже когда он двигает мышь."""
    try:
        from pynput import keyboard
    except Exception:
        return False

    def on_hotkey():
        if bot.is_running():
            bot.stop()
            hub.log("⏹ Нажата F10 — останавливаю бота", "warn")

    listener = keyboard.GlobalHotKeys({"<f10>": on_hotkey})
    listener.daemon = True
    listener.start()
    return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    hub.log("🚀 Сервер запущен. Открой вкладку «Экран», чтобы сделать первый снимок.")
    if _start_stop_hotkey():
        hub.log("⌨ Аварийная остановка бота: клавиша F10 (или мышь в левый верхний угол)")
    yield


app = FastAPI(title="Конструктор ботов", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------- статус и запуск

@app.get("/api/status")
def status():
    return {
        "running": bot.is_running(),
        "scenario": bot.scenario_name if bot.is_running() else "",
        "training": detector.train_status(),
        "capabilities": {
            "screen": vision.mss is not None and vision.np is not None,
            "opencv": vision.cv2 is not None,
            "mouse": engine.pyautogui is not None,
            "neural": detector.torch_available(),
        },
    }


class RunBody(BaseModel):
    scenario: dict


@app.post("/api/run")
def run_scenario(body: RunBody):
    if not body.scenario.get("blocks"):
        raise HTTPException(400, "Сценарий пуст — добавь блоки")
    if not bot.start(body.scenario):
        raise HTTPException(409, "Бот уже работает — сначала останови его")
    return {"ok": True}


@app.post("/api/stop")
def stop_scenario():
    bot.stop()
    return {"ok": True}


# ---------------------------------------------------------------- сценарии

@app.get("/api/scenarios")
def scenarios_list():
    return [p.stem for p in sorted(storage.SCENARIOS_DIR.glob("*.json"))]


@app.get("/api/scenarios/{name}")
def scenario_get(name: str):
    data = storage.load_json(storage.SCENARIOS_DIR / f"{storage.safe_name(name)}.json")
    if data is None:
        raise HTTPException(404, "Сценарий не найден")
    return data


class ScenarioBody(BaseModel):
    scenario: dict


@app.post("/api/scenarios/{name}")
def scenario_save(name: str, body: ScenarioBody):
    fname = storage.safe_name(name)
    if not fname:
        raise HTTPException(400, "Дай сценарию имя")
    body.scenario["name"] = name.strip()
    storage.save_json(storage.SCENARIOS_DIR / f"{fname}.json", body.scenario)
    return {"ok": True, "name": fname}


@app.delete("/api/scenarios/{name}")
def scenario_delete(name: str):
    p = storage.SCENARIOS_DIR / f"{storage.safe_name(name)}.json"
    if p.exists():
        p.unlink()
    return {"ok": True}


# ---------------------------------------------------------------- экран

def _jpeg_response(img, quality: int = 75) -> Response:
    return Response(vision.encode_jpeg(img, quality), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/screen.jpg")
def screen_live():
    try:
        img, _ = vision.capture_screen()
        return _jpeg_response(vision.resize_width(img, 1280), 70)
    except VisionError as e:
        raise HTTPException(503, str(e))


class CaptureBody(BaseModel):
    delay: float = 0


@app.post("/api/frame/capture")
def frame_capture(body: CaptureBody):
    delay = min(30.0, max(0.0, body.delay))
    if delay:
        time.sleep(delay)
    try:
        img, _ = vision.capture_screen()
    except VisionError as e:
        raise HTTPException(503, str(e))
    with _frame_lock:
        _frozen["img"] = img
    return {"w": int(img.shape[1]), "h": int(img.shape[0])}


@app.get("/api/frame.jpg")
def frame_get():
    with _frame_lock:
        img = _frozen["img"]
    if img is None:
        raise HTTPException(404, "Сначала сделай снимок")
    return _jpeg_response(img, 85)


# ---------------------------------------------------------------- картинки-образцы

class TemplateBody(BaseModel):
    name: str
    x: int
    y: int
    w: int
    h: int


@app.post("/api/templates")
def template_save(body: TemplateBody):
    fname = storage.safe_name(body.name)
    if not fname:
        raise HTTPException(400, "Дай образцу имя")
    with _frame_lock:
        img = _frozen["img"]
    if img is None:
        raise HTTPException(404, "Сначала сделай снимок")
    H, W = img.shape[:2]
    x0, y0 = max(0, body.x), max(0, body.y)
    x1, y1 = min(W, body.x + body.w), min(H, body.y + body.h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise HTTPException(400, "Выдели область побольше")
    vision.save_png(storage.TEMPLATES_DIR / f"{fname}.png", img[y0:y1, x0:x1].copy())
    hub.log(f"🖼 Сохранён образец «{fname}» ({x1 - x0}×{y1 - y0})")
    return {"ok": True, "name": fname}


@app.get("/api/templates")
def templates_list():
    return [p.stem for p in sorted(storage.TEMPLATES_DIR.glob("*.png"))]


@app.get("/api/templates/{name}.png")
def template_image(name: str):
    p = storage.TEMPLATES_DIR / f"{storage.safe_name(name)}.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.delete("/api/templates/{name}")
def template_delete(name: str):
    p = storage.TEMPLATES_DIR / f"{storage.safe_name(name)}.png"
    if p.exists():
        p.unlink()
    return {"ok": True}


# ---------------------------------------------------------------- датасет для обучения

class BoxesBody(BaseModel):
    boxes: list[dict]


def _clean_boxes(raw: list[dict]) -> list[dict]:
    out = []
    for b in raw:
        try:
            x, y, w, h = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
            cls = storage.safe_name(str(b.get("cls", ""))) or "объект"
        except (KeyError, ValueError, TypeError):
            continue
        if w >= 6 and h >= 6:
            out.append({"x": x, "y": y, "w": w, "h": h, "cls": cls})
    return out


@app.post("/api/dataset/save")
def dataset_save(body: BoxesBody):
    with _frame_lock:
        img = _frozen["img"]
    if img is None:
        raise HTTPException(404, "Сначала сделай снимок")
    boxes = _clean_boxes(body.boxes)
    if not boxes:
        raise HTTPException(400, "Обведи хотя бы один объект рамкой")
    sid = storage.new_id("shot")
    vision.save_png(storage.DATASET_IMAGES_DIR / f"{sid}.png", img)
    storage.save_json(storage.DATASET_LABELS_DIR / f"{sid}.json", {"boxes": boxes})
    hub.log(f"📚 Снимок добавлен в обучение ({len(boxes)} рамок)")
    return {"ok": True, "id": sid}


@app.get("/api/dataset")
def dataset_list():
    return [
        {"id": it["id"], "boxes": len(it["boxes"]),
         "classes": sorted({b.get("cls", "") for b in it["boxes"]} - {""})}
        for it in detector.dataset_items()
    ]


@app.get("/api/dataset/{sid}/image.png")
def dataset_image(sid: str):
    p = storage.DATASET_IMAGES_DIR / f"{storage.safe_name(sid)}.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.get("/api/dataset/{sid}/thumb.jpg")
def dataset_thumb(sid: str):
    p = storage.DATASET_IMAGES_DIR / f"{storage.safe_name(sid)}.png"
    if not p.exists():
        raise HTTPException(404)
    try:
        return _jpeg_response(vision.resize_width(vision.read_image(p), 340), 70)
    except VisionError as e:
        raise HTTPException(503, str(e))


@app.get("/api/dataset/{sid}/labels")
def dataset_labels_get(sid: str):
    data = storage.load_json(storage.DATASET_LABELS_DIR / f"{storage.safe_name(sid)}.json",
                             {"boxes": []})
    return data


@app.put("/api/dataset/{sid}/labels")
def dataset_labels_put(sid: str, body: BoxesBody):
    sid = storage.safe_name(sid)
    if not (storage.DATASET_IMAGES_DIR / f"{sid}.png").exists():
        raise HTTPException(404, "Снимок не найден")
    storage.save_json(storage.DATASET_LABELS_DIR / f"{sid}.json",
                      {"boxes": _clean_boxes(body.boxes)})
    return {"ok": True}


@app.delete("/api/dataset/{sid}")
def dataset_delete(sid: str):
    sid = storage.safe_name(sid)
    for p in (storage.DATASET_IMAGES_DIR / f"{sid}.png",
              storage.DATASET_LABELS_DIR / f"{sid}.json"):
        if p.exists():
            p.unlink()
    return {"ok": True}


# ---------------------------------------------------------------- обучение и модели

class TrainBody(BaseModel):
    name: str
    epochs: int = 15


@app.post("/api/train")
def train_start(body: TrainBody):
    name = storage.safe_name(body.name)
    if not name:
        raise HTTPException(400, "Дай модели имя")
    try:
        detector.start_training(name, min(100, max(3, body.epochs)), hub.log)
    except VisionError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/models")
def models_list():
    return detector.list_models()


@app.delete("/api/models/{name}")
def model_delete(name: str):
    detector.delete_model(storage.safe_name(name))
    return {"ok": True}


class DetectBody(BaseModel):
    model: str
    class_name: str = ""
    conf: float = 0.6


@app.post("/api/detect_test")
def detect_test(body: DetectBody):
    """Проверка модели: снимает экран, ищет объекты, возвращает картинку и рамки."""
    try:
        img, _ = vision.capture_screen()
        boxes = detector.detect(img, storage.safe_name(body.model),
                                body.class_name.strip() or None,
                                min(0.95, max(0.3, body.conf)))
    except VisionError as e:
        raise HTTPException(400, str(e))
    small = vision.resize_width(img, 1280)
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(vision.encode_jpeg(small, 75)).decode(),
        "scale": small.shape[1] / img.shape[1],
        "boxes": boxes,
    }


# ---------------------------------------------------------------- журнал

@app.websocket("/ws")
async def ws_log(ws: WebSocket):
    await ws.accept()
    for item in list(hub.history)[-200:]:
        await ws.send_json(item)
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
