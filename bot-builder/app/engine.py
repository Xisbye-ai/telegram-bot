"""Исполнитель сценариев: выполняет блоки по порядку в отдельном потоке."""
from __future__ import annotations

import csv
import random
import re
import threading
import time

from . import detector, ocr, overlay, storage, vision
from .vision import VisionError

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # мышь в левый верхний угол экрана = аварийный стоп
    pyautogui.PAUSE = 0        # темп задаём сами (паузы и плавные движения)
except Exception:
    pyautogui = None
try:
    import pyperclip
except Exception:
    pyperclip = None


class StopScenario(Exception):
    """Мягкая остановка: кнопка «Стоп» или блок «Стоп»."""


class BotEngine:
    def __init__(self, hub):
        self._hub = hub
        self._log = hub.log
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.scenario_name = ""
        self._hud: dict[int, str] = {}  # строки HUD: номер → текст
        self._window = ""               # часть заголовка окна игры ("" — весь экран)
        self._clicked: list = []        # память кликов: (x, y, время) — для «не кликать повторно»
        self._collect_hard = False      # сохранять кадры, где нейросеть ничего не нашла
        self._hard_last = 0.0
        # «глаза бота» для живого просмотра: найденные цели и клики (в пикселях экрана)
        self.last_overlay: dict = {"t": 0.0, "boxes": [], "clicks": []}

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
        self._window = str(scenario.get("window", "") or "").strip()
        self._clicked = []
        self.last_overlay = {"t": 0.0, "boxes": [], "clicks": []}
        self._collect_hard = bool(storage.load_settings().get("collect_hard"))
        self._log(f"▶️ Запуск сценария «{self.scenario_name}» ({len(blocks)} блоков)"
                  + (f" — в окне «{self._window}»" if self._window else ""))
        # «Найти…» записывает сюда, где нашёл; «Клик», «Если найдено»
        # и «Для каждого найденного» это читают. vars — счётчики, ocr — прочитанный текст
        ctx = {"found": None, "found_list": [], "success": None, "vars": {}, "ocr": ""}
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
        finally:
            self._hub.exec_block(None)

    def _capture(self):
        """Кадр для поиска: окно игры, если сценарий к нему привязан, иначе весь экран."""
        if self._window:
            return vision.capture_window(self._window)
        return vision.capture_screen()

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
        if block.get("id"):
            self._hub.exec_block(block["id"])  # подсветка выполняемого блока в редакторе
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

    def _search(self, p: dict, ctx: dict, pad: str, search_frame, on_miss=None) -> None:
        """Общий цикл «проверить один раз / ждать, пока появится».
        search_frame(кадр) возвращает список находок (лучшие первыми)."""
        mode = p.get("mode", "once")
        timeout = float(p.get("timeout", 0) or 0)
        interval = max(0.1, float(p.get("interval", 0.7) or 0.7))
        region = self._region_rect(p.get("region") or "")
        start = time.time()
        attempt = 0
        while True:
            self._check_stop()
            attempt += 1
            frame, (fx, fy) = self._capture()
            full_w, full_h = frame.shape[1], frame.shape[0]
            offx, offy = fx, fy
            if region is not None:
                rx, ry, rw, rh = region
                frame, (cx0, cy0) = self._crop(frame, (rx - fx, ry - fy, rw, rh))
                offx, offy = fx + cx0, fy + cy0
            hits = search_frame(frame) if frame.size else []
            if hits:
                # координаты в пикселях экрана (для «глаз бота» и пересчёта в мышь)
                phys = [{"x": h["x"] + offx, "y": h["y"] + offy, "w": h["w"], "h": h["h"]}
                        for h in hits]
                self.last_overlay = {
                    "t": time.time(),
                    "boxes": [(b["x"], b["y"], b["w"], b["h"]) for b in phys],
                    "clicks": self.last_overlay["clicks"],
                }
                sx, sy = (1.0, 1.0) if self._window else self._mouse_scale(full_w, full_h)
                ctx["found_list"] = [{
                    "x": b["x"] * sx, "y": b["y"] * sy, "w": b["w"] * sx, "h": b["h"] * sy,
                    "px": b["x"] + b["w"] / 2, "py": b["y"] + b["h"] / 2,
                } for b in phys]
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
                if on_miss:
                    on_miss(frame)
                return
            if timeout and time.time() - start > timeout:
                ctx["found"], ctx["found_list"], ctx["success"] = None, [], False
                self._log(f"{pad}⏱ Не найдено за {timeout:.0f} сек — иду дальше", "warn")
                if on_miss:
                    on_miss(frame)
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

    @staticmethod
    def _num_str(v) -> str:
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)

    def _fmt(self, text, ctx: dict) -> str:
        """Подставляет {счётчики} и служебные {время}, {найдено}, {текст} в строку."""
        def repl(m):
            name = m.group(1).strip()
            if name == "время":
                return time.strftime("%H:%M:%S")
            if name == "найдено":
                return str(len(ctx.get("found_list") or []))
            if name == "текст":
                return str(ctx.get("ocr", ""))
            if name in ctx["vars"]:
                return self._num_str(ctx["vars"][name])
            return m.group(0)
        return re.sub(r"\{([^{}]+)\}", repl, str(text))

    def _hud_push(self) -> None:
        lines = [self._hud[k] for k in sorted(self._hud)]
        self._hub.hud(lines)          # в браузер (и на телефон)
        overlay.set_lines(lines)      # окошко поверх игры на ПК

    @staticmethod
    def _region_rect(name: str):
        """Именованная область экрана → (x, y, w, h) в пикселях снимка."""
        if not name:
            return None
        r = storage.load_regions().get(name)
        if not r:
            raise VisionError(f"Область «{name}» не найдена — сохрани её на вкладке «Экран»")
        return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])

    @staticmethod
    def _crop(frame, rect):
        x, y, w, h = rect
        fh, fw = frame.shape[:2]
        x, y = max(0, min(x, fw - 1)), max(0, min(y, fh - 1))
        return frame[y:min(fh, y + h), x:min(fw, x + w)], (x, y)

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

        self._search(p, ctx, pad, search, on_miss=self._maybe_hard_frame)

    def _maybe_hard_frame(self, frame) -> None:
        """Сбор сложных кадров: нейросеть ничего не нашла — сохраняем кадр на доразметку."""
        if not self._collect_hard or frame is None or not getattr(frame, "size", 0):
            return
        now = time.time()
        if now - self._hard_last < 90:
            return
        if len(list(storage.DATASET_IMAGES_DIR.glob("hard_*.png"))) >= 40:
            return
        sid = storage.new_id("hard")
        try:
            vision.save_png(storage.DATASET_IMAGES_DIR / f"{sid}.png", frame)
            storage.save_json(storage.DATASET_LABELS_DIR / f"{sid}.json", {"boxes": []})
        except Exception:
            return
        self._hard_last = now
        self._log("🧪 Нейросеть ничего не нашла — кадр сохранён в обучение, разметь его позже", "warn")

    # ---------------------------------------------------------------- блоки: мышь и клавиатура

    def _move_human(self, x, y, duration: float) -> None:
        """Движение мыши по изогнутой кривой с плавным разгоном — как рука, а не линейка."""
        duration = max(0.0, duration)
        x0, y0 = pyautogui.position()
        dist = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
        if duration < 0.08 or dist < 4:
            pyautogui.moveTo(x, y, duration=duration)
            return
        k = random.uniform(-0.22, 0.22)
        cx = (x0 + x) / 2 - (y - y0) * k
        cy = (y0 + y) / 2 + (x - x0) * k
        steps = max(8, min(36, int(dist / 25)))
        for i in range(1, steps + 1):
            self._check_stop()
            t = i / steps
            t = t * t * (3 - 2 * t)
            bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x
            by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y
            pyautogui.moveTo(int(bx), int(by))
            time.sleep(duration / steps)

    def _remember_click(self, pt, ctx, p) -> None:
        """Точка клика для «глаз бота» на живом просмотре."""
        f = ctx.get("found") if p.get("target", "found") == "found" else None
        px, py = (f["px"], f["py"]) if f and "px" in f else pt
        self.last_overlay["clicks"] = (self.last_overlay["clicks"] + [(px, py, time.time())])[-12:]

    def _b_click(self, p, block, ctx, depth, pad):
        self._need_gui()
        pt = self._target_point(p, ctx)
        if pt is None:
            self._log(f"{pad}⚠️ Клик пропущен: ничего не найдено", "warn")
            return
        cooldown = max(0.0, float(p.get("cooldown", 0) or 0))
        if cooldown > 0:
            # память кликов: не кликать снова туда, где недавно уже кликали
            now = time.time()
            self._clicked = [(x, y, exp) for x, y, exp in self._clicked if exp > now][-300:]
            f = ctx.get("found") if p.get("target", "found") == "found" else None
            radius = max(20.0, (f["w"] + f["h"]) / 4) if f else 30.0
            if any((x - pt[0]) ** 2 + (y - pt[1]) ** 2 < radius * radius
                   for x, y, _ in self._clicked):
                self._log(f"{pad}🔁 Рядом с ({pt[0]}, {pt[1]}) уже кликал — пропускаю "
                          f"(память {cooldown:g} сек)")
                return
        jitter = int(float(p.get("jitter", 0) or 0))
        if jitter > 0:  # клик не в одну и ту же точку — выглядит естественнее
            pt = (pt[0] + random.randint(-jitter, jitter),
                  pt[1] + random.randint(-jitter, jitter))
        self._move_human(pt[0], pt[1], float(p.get("move_duration", 0.15) or 0))
        btn = p.get("button", "left")
        if btn == "double":
            pyautogui.doubleClick()
        elif btn == "right":
            pyautogui.click(button="right")
        else:
            pyautogui.click()
        if cooldown > 0:
            self._clicked.append((pt[0], pt[1], time.time() + cooldown))
        self._remember_click(pt, ctx, p)
        names = {"left": "левый", "right": "правый", "double": "двойной"}
        self._log(f"{pad}🖱 Клик ({names.get(btn, btn)}) в ({pt[0]}, {pt[1]})")

    def _b_move_mouse(self, p, block, ctx, depth, pad):
        self._need_gui()
        pt = self._target_point(p, ctx)
        if pt is None:
            self._log(f"{pad}⚠️ Перемещение пропущено: ничего не найдено", "warn")
            return
        self._move_human(pt[0], pt[1], max(0.0, float(p.get("duration", 0.3) or 0)))
        self._log(f"{pad}🖱 Мышь в ({pt[0]}, {pt[1]})")

    def _b_drag_mouse(self, p, block, ctx, depth, pad):
        self._need_gui()
        pt = self._target_point(p, ctx)
        if pt is None:
            self._log(f"{pad}⚠️ Перетаскивание пропущено: ничего не найдено", "warn")
            return
        x2 = int(float(p.get("x2", 0) or 0))
        y2 = int(float(p.get("y2", 0) or 0))
        dur = max(0.1, float(p.get("duration", 0.5) or 0.5))
        self._move_human(pt[0], pt[1], 0.15)
        pyautogui.mouseDown()
        try:
            pyautogui.moveTo(x2, y2, duration=dur)
        finally:
            pyautogui.mouseUp()
        self._log(f"{pad}🖱 Перетащил из ({pt[0]}, {pt[1]}) в ({x2}, {y2})")

    def _b_hold_key(self, p, block, ctx, depth, pad):
        self._need_gui()
        key = str(p.get("keys", "")).strip().lower()
        sec = max(0.05, float(p.get("seconds", 1) or 1))
        if not key:
            return
        self._log(f"{pad}⌨️ Держу «{key}» {sec:g} сек")
        pyautogui.keyDown(key)
        try:
            self._sleep(sec)
        finally:
            pyautogui.keyUp(key)  # отпускаем даже при остановке бота

    def _b_scroll(self, p, block, ctx, depth, pad):
        self._need_gui()
        amount = int(float(p.get("amount", -500) or 0))
        pyautogui.scroll(amount)
        self._log(f"{pad}🖲 Прокрутка {'вверх' if amount > 0 else 'вниз'} ({amount})")

    def _b_type_text(self, p, block, ctx, depth, pad):
        self._need_gui()
        text = self._fmt(p.get("text", ""), ctx)
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
        rand = max(0.0, float(p.get("rand", 0) or 0))
        if rand > 0:  # случайный разброс, чтобы бот не действовал как метроном
            sec = max(0.0, sec + random.uniform(-rand, rand))
        self._log(f"{pad}⏳ Пауза {sec:.1f} сек")
        self._sleep(sec)

    def _b_log(self, p, block, ctx, depth, pad):
        self._log(f"{pad}💬 {self._fmt(p.get('message', ''), ctx)}")

    def _b_stop(self, p, block, ctx, depth, pad):
        self._log(f"{pad}⏹ Блок «Стоп»")
        raise StopScenario()

    def _b_if_pixel(self, p, block, ctx, depth, pad):
        """Проверка цвета пикселя — например, «полоска здоровья ещё красная?»."""
        x = int(float(p.get("x", 0) or 0))
        y = int(float(p.get("y", 0) or 0))
        want = str(p.get("color", "#ffffff")).lstrip("#")
        tol = max(0, int(float(p.get("tolerance", 12) or 0)))
        try:
            wr, wg, wb = int(want[0:2], 16), int(want[2:4], 16), int(want[4:6], 16)
        except (ValueError, IndexError):
            raise VisionError(f"Непонятный цвет «{p.get('color')}» — нужен вид #rrggbb")
        frame, (fx, fy) = self._capture()
        fh, fw = frame.shape[:2]
        x_in, y_in = x - fx, y - fy
        if not (0 <= x_in < fw and 0 <= y_in < fh):
            raise VisionError(f"Точка ({x}, {y}) за границей снимка {fw}×{fh}")
        b, g, r = (int(v) for v in frame[y_in, x_in])
        match = abs(r - wr) <= tol and abs(g - wg) <= tol and abs(b - wb) <= tol
        got = f"#{r:02x}{g:02x}{b:02x}"
        if match:
            self._log(f"{pad}🎨 Пиксель ({x}, {y}) = {got} — совпал → ветка «да»")
            self._run_blocks(block.get("then", []), ctx, depth + 1)
        else:
            self._log(f"{pad}🎨 Пиксель ({x}, {y}) = {got}, ждали #{want} → ветка «нет»")
            self._run_blocks(block.get("els", []), ctx, depth + 1)

    def _b_ocr_read(self, p, block, ctx, depth, pad):
        """Читает текст из области экрана и кладёт его в счётчик."""
        region = self._region_rect(p.get("region") or "")
        if region is None:
            raise VisionError("В блоке «Прочитать текст» не выбрана область — сохрани её на вкладке «Экран»")
        digits = p.get("digits", "no") == "yes"
        var = str(p.get("var", "") or "текст").strip()
        frame, (fx, fy) = self._capture()
        rx, ry, rw, rh = region
        crop, _ = self._crop(frame, (rx - fx, ry - fy, rw, rh))
        text = ocr.read_text(crop, digits=digits)
        ctx["ocr"] = text
        value: object = text
        num = text.replace(" ", "").replace(",", ".")
        try:
            value = float(num)
        except ValueError:
            pass
        ctx["vars"][var] = value
        shown = self._num_str(value) if value != "" else "(пусто)"
        self._log(f"{pad}🔤 Прочитал: «{shown}» → счётчик {{{var}}}")

    def _b_counter(self, p, block, ctx, depth, pad):
        name = str(p.get("name", "") or "счётчик").strip()
        action = p.get("action", "add")
        value = float(p.get("value", 1) or 0)
        if action == "set":
            ctx["vars"][name] = value
        else:
            old = ctx["vars"].get(name, 0)
            if not isinstance(old, (int, float)):
                old = 0
            ctx["vars"][name] = old + value
        self._log(f"{pad}🔢 {{{name}}} = {self._num_str(ctx['vars'][name])}")

    def _b_if_var(self, p, block, ctx, depth, pad):
        name = str(p.get("name", "") or "счётчик").strip()
        op = p.get("op", ">=")
        raw = p.get("value", 0)
        cur = ctx["vars"].get(name, 0)
        try:
            a, b = float(cur), float(raw)
        except (TypeError, ValueError):
            a, b = str(cur), str(raw)
        ops = {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b,
               "==": a == b, "!=": a != b}
        ok = ops.get(op, False)
        self._log(f"{pad}🔢 {{{name}}} = {self._num_str(cur)} {op} {self._num_str(raw)} → "
                  f"{'да' if ok else 'нет'}")
        self._run_blocks(block.get("then" if ok else "els", []), ctx, depth + 1)

    def _b_signal(self, p, block, ctx, depth, pad):
        msg = self._fmt(p.get("message", "") or "Сигнал от бота", ctx)
        self._hub.beep(msg)
        self._log(f"{pad}🔔 {msg}", "warn")

    def _b_hud_show(self, p, block, ctx, depth, pad):
        line = max(1, min(9, int(float(p.get("line", 1) or 1))))
        text = self._fmt(p.get("text", ""), ctx)
        self._hud[line] = text
        self._hud_push()
        self._log(f"{pad}🖥 HUD строка {line}: {text}")

    def _b_hud_clear(self, p, block, ctx, depth, pad):
        line = int(float(p.get("line", 0) or 0))
        if line <= 0:
            self._hud.clear()
        else:
            self._hud.pop(line, None)
        self._hud_push()
        self._log(f"{pad}🖥 HUD очищен" + ("" if line <= 0 else f" (строка {line})"))

    def _b_stats_write(self, p, block, ctx, depth, pad):
        note = self._fmt(p.get("note", ""), ctx)
        counters = "; ".join(f"{k}={self._num_str(v)}" for k, v in ctx["vars"].items())
        is_new = not storage.STATS_FILE.exists()
        with storage.STATS_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["время", "сценарий", "заметка", "счётчики"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), self.scenario_name, note, counters])
        self._log(f"{pad}📊 Записал в статистику: {note or counters or '—'}")

    def _b_for_each(self, p, block, ctx, depth, pad):
        items = list(ctx.get("found_list") or [])
        if not items:
            self._log(f"{pad}⚠️ Список находок пуст — «Для каждого» пропущен", "warn")
            return
        order = p.get("order", "score")
        if order == "random":
            random.shuffle(items)  # человечнее: цели в случайном порядке
        elif order == "top":
            items.sort(key=lambda f: f["y"])
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
