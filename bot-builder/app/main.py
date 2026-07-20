"""Веб-сервер: отдаёт интерфейс конструктора и API для него."""
from __future__ import annotations

import asyncio
import base64
import csv
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import detector, engine, ocr, storage, vision
from .vision import VisionError

storage.ensure_dirs()
STATIC_DIR = Path(__file__).parent / "static"


class LogHub:
    """Живые события для открытых вкладок (WebSocket): журнал, HUD, сигналы."""

    def __init__(self):
        self.history: deque = deque(maxlen=400)
        self.hud_lines: list[str] = []
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def log(self, msg: str, level: str = "info") -> None:
        item = {"kind": "log", "time": time.strftime("%H:%M:%S"),
                "msg": str(msg), "level": level}
        self.history.append(item)
        self._post(item)

    def hud(self, lines: list[str]) -> None:
        self.hud_lines = list(lines)
        self._post({"kind": "hud", "lines": self.hud_lines})

    def beep(self, msg: str) -> None:
        self._post({"kind": "beep", "msg": str(msg)})

    def _post(self, item: dict) -> None:
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
bot = engine.BotEngine(hub)

# «замороженный» снимок экрана: из него вырезаются образцы и снимки для обучения
_frame_lock = threading.Lock()
_frozen: dict = {"img": None}

# последний запущенный сценарий — его стартует горячая клавиша
_last_scenario: dict = {"scenario": storage.load_json(storage.LAST_SCENARIO_FILE)}


def _remember_scenario(scenario: dict) -> None:
    _last_scenario["scenario"] = scenario
    try:
        storage.save_json(storage.LAST_SCENARIO_FILE, scenario)
    except OSError:
        pass


# ---------------------------------------------------------------- горячие клавиши

def hotkey_toggle() -> None:
    """Запустить последний сценарий, а если бот уже работает — остановить."""
    if bot.is_running():
        bot.stop()
        hub.log("⏹ Горячая клавиша — останавливаю бота", "warn")
        return
    sc = _last_scenario["scenario"]
    if not sc or not sc.get("blocks"):
        hub.log("⌨ Нечего запускать: сначала запусти сценарий один раз кнопкой ▶", "warn")
        return
    if bot.start(sc):
        hub.log(f"⌨ Горячая клавиша — запускаю «{sc.get('name', 'Без имени')}»")


def hotkey_stop() -> None:
    if bot.is_running():
        bot.stop()
        hub.log("⏹ Аварийная клавиша — останавливаю бота", "warn")


def hotkey_shot(mode: str) -> None:
    """Снимок экрана из игры: замораживает кадр и открывает нужный режим в браузере."""
    try:
        img, _ = vision.capture_screen()
    except VisionError as e:
        hub.log(f"⌨ Снимок не получился: {e}", "error")
        return
    with _frame_lock:
        _frozen["img"] = img
    what = "разметки (обучение нейросети)" if mode == "label" else "выделения области или образца"
    hub.log(f"📸 Горячая клавиша — снимок сделан, открываю режим {what}")
    hub._post({"kind": "goto", "tab": "screen", "mode": mode})


class HotkeyManager:
    """Глобальные горячие клавиши (pynput). Слушатель пересоздаётся при смене настроек."""

    ACTIONS = {
        "toggle": hotkey_toggle,
        "stop": hotkey_stop,
        "shot_label": lambda: hotkey_shot("label"),
        "shot_region": lambda: hotkey_shot("region"),
    }

    def __init__(self):
        self._listener = None

    @staticmethod
    def available() -> bool:
        try:
            import pynput  # noqa: F401
            return True
        except Exception:
            return False

    def apply(self, hotkeys: dict) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        bindings = {}
        for action, key in hotkeys.items():
            key = (key or "off").strip().lower()
            if key == "off" or action not in self.ACTIONS:
                continue
            bindings[f"<{key}>"] = self.ACTIONS[action]
        if not bindings:
            return True
        try:
            self._listener = keyboard.GlobalHotKeys(bindings)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as e:
            hub.log(f"⌨ Не удалось включить горячие клавиши: {e}", "error")
            return False


hotkeys = HotkeyManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    hub.log("🚀 Сервер запущен. Открой вкладку «Экран», чтобы сделать первый снимок.")
    hk = storage.load_settings()["hotkeys"]
    if hotkeys.available() and hotkeys.apply(hk):
        active = ", ".join(f"{v.upper()}" for v in hk.values() if v != "off")
        if active:
            hub.log(f"⌨ Горячие клавиши работают: {active} (настройка — кнопка ⌨ вверху)")
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
            "ocr": ocr.available(),
            "hotkeys": hotkeys.available(),
        },
        "last_scenario": (_last_scenario["scenario"] or {}).get("name", ""),
    }


ALLOWED_KEYS = {"off"} | {f"f{i}" for i in range(1, 13)}


@app.get("/api/settings")
def settings_get():
    return storage.load_settings()


class SettingsBody(BaseModel):
    hotkeys: dict


@app.post("/api/settings")
def settings_save(body: SettingsBody):
    hk = dict(storage.DEFAULT_HOTKEYS)
    for action, key in body.hotkeys.items():
        if action not in storage.DEFAULT_HOTKEYS:
            continue
        key = str(key or "off").strip().lower()
        if key not in ALLOWED_KEYS:
            raise HTTPException(400, f"Клавиша «{key}» не поддерживается — выбери F1–F12 или «выкл»")
        hk[action] = key
    used = [k for k in hk.values() if k != "off"]
    if len(used) != len(set(used)):
        raise HTTPException(400, "Одна клавиша назначена на два действия — выбери разные")
    settings = storage.load_settings()
    settings["hotkeys"] = hk
    storage.save_settings(settings)
    if hotkeys.available():
        hotkeys.apply(hk)
        hub.log("⌨ Горячие клавиши обновлены: " +
                ", ".join(f"{v.upper()}" for v in hk.values() if v != "off"))
    return {"ok": True, "hotkeys": hk}


class RunBody(BaseModel):
    scenario: dict


@app.post("/api/run")
def run_scenario(body: RunBody):
    if not body.scenario.get("blocks"):
        raise HTTPException(400, "Сценарий пуст — добавь блоки")
    if not bot.start(body.scenario):
        raise HTTPException(409, "Бот уже работает — сначала останови его")
    _remember_scenario(body.scenario)  # этот сценарий будет запускать горячая клавиша
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


# ---------------------------------------------------------------- области и пиксели

class RegionBody(BaseModel):
    name: str
    x: int
    y: int
    w: int
    h: int


@app.get("/api/regions")
def regions_list():
    return storage.load_regions()


@app.post("/api/regions")
def region_save(body: RegionBody):
    name = storage.safe_name(body.name)
    if not name:
        raise HTTPException(400, "Дай области имя")
    if body.w < 4 or body.h < 4:
        raise HTTPException(400, "Выдели область побольше")
    regions = storage.load_regions()
    regions[name] = {"x": max(0, body.x), "y": max(0, body.y), "w": body.w, "h": body.h}
    storage.save_regions(regions)
    hub.log(f"📐 Сохранена область «{name}» ({body.w}×{body.h})")
    return {"ok": True, "name": name}


@app.delete("/api/regions/{name}")
def region_delete(name: str):
    regions = storage.load_regions()
    regions.pop(storage.safe_name(name), None)
    storage.save_regions(regions)
    return {"ok": True}


@app.get("/api/pixel")
def pixel_color(x: int, y: int):
    """Точный цвет пикселя на замороженном снимке — для блока «Если цвет пикселя»."""
    with _frame_lock:
        img = _frozen["img"]
    if img is None:
        raise HTTPException(404, "Сначала сделай снимок")
    h, w = img.shape[:2]
    x, y = max(0, min(w - 1, x)), max(0, min(h - 1, y))
    b, g, r = (int(v) for v in img[y, x])
    return {"x": x, "y": y, "color": f"#{r:02x}{g:02x}{b:02x}"}


class OcrBody(BaseModel):
    x: int
    y: int
    w: int
    h: int
    digits: bool = False


@app.post("/api/ocr_test")
def ocr_test(body: OcrBody):
    """Пробное чтение текста из выделенной области замороженного снимка."""
    with _frame_lock:
        img = _frozen["img"]
    if img is None:
        raise HTTPException(404, "Сначала сделай снимок")
    h, w = img.shape[:2]
    x0, y0 = max(0, body.x), max(0, body.y)
    x1, y1 = min(w, body.x + body.w), min(h, body.y + body.h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise HTTPException(400, "Выдели область побольше")
    try:
        text = ocr.read_text(img[y0:y1, x0:x1], digits=body.digits)
    except VisionError as e:
        raise HTTPException(400, str(e))
    return {"text": text}


# ---------------------------------------------------------------- статистика

@app.get("/api/stats")
def stats_rows(limit: int = 200):
    if not storage.STATS_FILE.exists():
        return {"header": [], "rows": []}
    with storage.STATS_FILE.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0] if rows else []
    body = rows[1:][-max(1, min(1000, limit)):]
    body.reverse()  # свежие сверху
    return {"header": header, "rows": body}


@app.get("/api/stats.csv")
def stats_download():
    if not storage.STATS_FILE.exists():
        raise HTTPException(404, "Статистики пока нет")
    return FileResponse(storage.STATS_FILE, filename="stats.csv",
                        headers={"Cache-Control": "no-store"})


@app.delete("/api/stats")
def stats_clear():
    if storage.STATS_FILE.exists():
        storage.STATS_FILE.unlink()
    return {"ok": True}


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
    if hub.hud_lines:
        await ws.send_json({"kind": "hud", "lines": hub.hud_lines})
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
