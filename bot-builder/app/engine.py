"""Исполнитель сценариев: выполняет блоки по порядку в отдельном потоке."""
from __future__ import annotations

import threading
import time

from . import detector, vision
from .vision import VisionError

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # мышь в левый верхний угол экрана = аварийный стоп
except Exception:
    pyautogui = None
try:
    import pyperclip
except Exception:
    pyperclip = None


class StopScenario(Exception):
    """Мягкая остановка: кнопка «Стоп» или блок «Стоп»."""


class BotEngine:
    def __init__(self, log):
        self._log = log
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.scenario_name = ""

    # ---------------------------------------------------------------- управление

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, scenario: dict) -> bool:
        if self.is_running():
            return False
        self._stop.clear()
        self.scenario_name = scenario.get("name") or "Без имени"
        self._thread = threading.Thread(target=self._run, args=(scenario,), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------------------- выполнение

    def _run(self, scenario: dict) -> None:
        blocks = scenario.get("blocks", [])
        self._log(f"▶️ Запуск сценария «{self.scenario_name}» ({len(blocks)} блоков)")
        # «Найти…» записывает сюда, где нашёл; «Клик», «Если найдено»
        # и «Для каждого найденного» это читают
        ctx = {"found": None, "found_list": [], "success": None}
        try:
            self._run_blocks(blocks, ctx, depth=0)
            self._log("🏁 Сценарий завершён")
        except StopScenario:
            self._log("⏹ Сценарий остановлен")
        except VisionError as e:
            self._log(f"❌ {e}", "error")
        except Exception as e:  # noqa: BLE001 — показываем любую ошибку в журнале
            if pyautogui is not None and isinstance(e, pyautogui.FailSafeException):
                self._log("⏹ Аварийная остановка: мышь в левом верхнем углу экрана", "warn")
            else:
                self._log(f"❌ Ошибка: {type(e).__name__}: {e}", "error")

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise StopScenario()

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self._check_stop()
            time.sleep(min(0.2, max(0.01, end - time.time())))

    def _run_blocks(self, blocks: list, ctx: dict, depth: int) -> None:
        for block in blocks:
            self._check_stop()
            self._exec(block, ctx, depth)

    def _exec(self, block: dict, ctx: dict, depth: int) -> None:
        t = block.get("type", "")
        p = block.get("params") or {}
        pad = "· " * depth
        handler = getattr(self, f"_b_{t}", None)
        if handler is None:
            self._log(f"{pad}⚠️ Неизвестный блок «{t}» — пропускаю", "warn")
            return
        handler(p, block, ctx, depth, pad)

    # ---------------------------------------------------------------- вспомогательное

    def _need_gui(self) -> None:
        if pyautogui is None:
            raise VisionError(
                "pyautogui не установлен — мышь и клавиатура недоступны. Выполни: pip install pyautogui"
            )

    @staticmethod
    def _mouse_scale(frame_w: int, frame_h: int):
        """Пересчёт из пикселей снимка в координаты мыши (важно при масштабе Windows 125–150%)."""
        if pyautogui is None:
            return 1.0, 1.0
        sw, sh = pyautogui.size()
        return sw / frame_w, sh / frame_h

    def _search(self, p: dict, ctx: dict, pad: str, search_frame) -> None:
        """Общий цикл «проверить один раз / ждать, пока появится».
        search_frame(кадр) возвращает список находок (лучшие первыми)."""
        mode = p.get("mode", "once")
        timeout = float(p.get("timeout", 0) or 0)
        interval = max(0.1, float(p.get("interval", 0.7) or 0.7))
        start = time.time()
        attempt = 0
        while True:
            self._check_stop()
            attempt += 1
            frame, (offx, offy) = vision.capture_screen()
            hits = search_frame(frame)
            if hits:
                sx, sy = self._mouse_scale(frame.shape[1], frame.shape[0])
                ctx["found_list"] = [{
                    "x": (h["x"] + offx) * sx, "y": (h["y"] + offy) * sy,
                    "w": h["w"] * sx, "h": h["h"] * sy,
                } for h in hits]
                ctx["found"] = ctx["found_list"][0]
                ctx["success"] = True
                cx = int(ctx["found"]["x"] + ctx["found"]["w"] / 2)
                cy = int(ctx["found"]["y"] + ctx["found"]["h"] / 2)
                best = f"({cx}, {cy}), совпадение {hits[0].get('score', 0):.2f}"
                if len(hits) > 1:
                    self._log(f"{pad}✅ Найдено {len(hits)} шт., лучшее в {best}")
                else:
                    self._log(f"{pad}✅ Найдено в {best}")
                return
            if mode != "wait":
                ctx["found"], ctx["found_list"], ctx["success"] = None, [], False
                self._log(f"{pad}➖ Не найдено")
                return
            if timeout and time.time() - start > timeout:
                ctx["found"], ctx["found_list"], ctx["success"] = None, [], False
                self._log(f"{pad}⏱ Не найдено за {timeout:.0f} сек — иду дальше", "warn")
                return
            if attempt % 10 == 1 and attempt > 1:
                self._log(f"{pad}🔎 Ищу… (попытка {attempt})")
            self._sleep(interval)

    def _target_point(self, p: dict, ctx: dict):
        if p.get("target", "found") == "coords":
            return int(float(p.get("x", 0) or 0)), int(float(p.get("y", 0) or 0))
        f = ctx.get("found")
        if not f:
            return None
        return int(f["x"] + f["w"] / 2), int(f["y"] + f["h"] / 2)

    # ---------------------------------------------------------------- блоки: поиск

    def _b_find_image(self, p, block, ctx, depth, pad):
        name = p.get("template") or ""
        if not name:
            raise VisionError("В блоке «Найти картинку» не выбран образец")
        thr = min(0.99, max(0.5, float(p.get("threshold", 0.85) or 0.85)))
        find_all = p.get("find_all", "first") == "all"
        tpl = vision.load_template(name)
        self._log(f"{pad}🔍 Ищу картинку «{name}» (точность ≥ {thr:.2f})")

        def search(frame):
            if find_all:
                return vision.find_template_all(frame, tpl, thr)
            hit = vision.find_template(frame, tpl, thr)
            return [hit] if hit else []

        self._search(p, ctx, pad, search)

    def _b_find_object(self, p, block, ctx, depth, pad):
        model = p.get("model") or ""
        if not model:
            raise VisionError("В блоке «Найти объект» не выбрана модель")
        cls = (p.get("class_name") or "").strip() or None
        conf = min(0.95, max(0.3, float(p.get("conf", 0.6) or 0.6)))
        find_all = p.get("find_all", "first") == "all"
        what = f"«{cls}»" if cls else "объекты"
        self._log(f"{pad}🧠 Нейросеть «{model}» ищет {what} (уверенность ≥ {conf:.2f})")

        def search(frame):
            hits = detector.detect(frame, model, cls, conf)
            return hits if find_all else hits[:1]

        self._search(p, ctx, pad, search)

    # ---------------------------------------------------------------- блоки: мышь и клавиатура

    def _b_click(self, p, block, ctx, depth, pad):
        self._need_gui()
        pt = self._target_point(p, ctx)
        if pt is None:
            self._log(f"{pad}⚠️ Клик пропущен: ничего не найдено", "warn")
            return
        pyautogui.moveTo(pt[0], pt[1], duration=max(0.0, float(p.get("move_duration", 0.15) or 0)))
        btn = p.get("button", "left")
        if btn == "double":
            pyautogui.doubleClick()
        elif btn == "right":
            pyautogui.click(button="right")
        else:
            pyautogui.click()
        names = {"left": "левый", "right": "правый", "double": "двойной"}
        self._log(f"{pad}🖱 Клик ({names.get(btn, btn)}) в ({pt[0]}, {pt[1]})")

    def _b_move_mouse(self, p, block, ctx, depth, pad):
        self._need_gui()
        pt = self._target_point(p, ctx)
        if pt is None:
            self._log(f"{pad}⚠️ Перемещение пропущено: ничего не найдено", "warn")
            return
        pyautogui.moveTo(pt[0], pt[1], duration=max(0.0, float(p.get("duration", 0.3) or 0)))
        self._log(f"{pad}🖱 Мышь в ({pt[0]}, {pt[1]})")

    def _b_scroll(self, p, block, ctx, depth, pad):
        self._need_gui()
        amount = int(float(p.get("amount", -500) or 0))
        pyautogui.scroll(amount)
        self._log(f"{pad}🖲 Прокрутка {'вверх' if amount > 0 else 'вниз'} ({amount})")

    def _b_type_text(self, p, block, ctx, depth, pad):
        self._need_gui()
        text = str(p.get("text", ""))
        if not text:
            return
        if text.isascii():
            pyautogui.typewrite(text, interval=0.02)
        else:
            # русский текст pyautogui печатать не умеет — вставляем через буфер обмена
            if pyperclip is None:
                raise VisionError("Для русского текста нужен pyperclip: pip install pyperclip")
            pyperclip.copy(text)
            self._sleep(0.1)
            pyautogui.hotkey("ctrl", "v")
        self._log(f"{pad}⌨️ Ввёл текст: {text[:40]}")

    def _b_press_key(self, p, block, ctx, depth, pad):
        self._need_gui()
        combo = str(p.get("keys", "")).strip().lower()
        if not combo:
            return
        parts = [k.strip() for k in combo.split("+") if k.strip()]
        if len(parts) > 1:
            pyautogui.hotkey(*parts)
        else:
            pyautogui.press(parts[0])
        self._log(f"{pad}⌨️ Нажал: {combo}")

    # ---------------------------------------------------------------- блоки: логика

    def _b_wait(self, p, block, ctx, depth, pad):
        sec = max(0.0, float(p.get("seconds", 1) or 0))
        self._log(f"{pad}⏳ Пауза {sec:g} сек")
        self._sleep(sec)

    def _b_log(self, p, block, ctx, depth, pad):
        self._log(f"{pad}💬 {p.get('message', '')}")

    def _b_stop(self, p, block, ctx, depth, pad):
        self._log(f"{pad}⏹ Блок «Стоп»")
        raise StopScenario()

    def _b_for_each(self, p, block, ctx, depth, pad):
        items = list(ctx.get("found_list") or [])
        if not items:
            self._log(f"{pad}⚠️ Список находок пуст — «Для каждого» пропущен", "warn")
            return
        for i, f in enumerate(items, 1):
            self._check_stop()
            ctx["found"], ctx["success"] = f, True
            self._log(f"{pad}📍 Цель {i} из {len(items)}")
            self._run_blocks(block.get("children", []), ctx, depth + 1)

    def _b_if_found(self, p, block, ctx, depth, pad):
        if ctx.get("success"):
            self._log(f"{pad}🔀 Найдено → ветка «да»")
            self._run_blocks(block.get("then", []), ctx, depth + 1)
        else:
            self._log(f"{pad}🔀 Не найдено → ветка «нет»")
            self._run_blocks(block.get("els", []), ctx, depth + 1)

    def _b_repeat(self, p, block, ctx, depth, pad):
        count = max(1, int(float(p.get("count", 3) or 1)))
        for i in range(1, count + 1):
            self._check_stop()
            self._log(f"{pad}🔁 Повтор {i} из {count}")
            self._run_blocks(block.get("children", []), ctx, depth + 1)

    def _b_loop_forever(self, p, block, ctx, depth, pad):
        i = 0
        while True:
            self._check_stop()
            i += 1
            self._log(f"{pad}♾ Круг {i}")
            self._run_blocks(block.get("children", []), ctx, depth + 1)
            self._sleep(0.05)
