/* Конструктор ботов — логика интерфейса (без внешних библиотек). */
'use strict';

const $ = s => document.querySelector(s);

function el(tag, cls, text) {
  const d = document.createElement(tag);
  if (cls) d.className = cls;
  if (text != null) d.textContent = text;
  return d;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!r.ok) {
    let msg = 'Ошибка ' + r.status;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* не JSON */ }
    throw new Error(msg);
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r;
}

let toastTimer = null;
function toast(msg, bad = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = bad ? 'bad' : '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 3000);
}

/* ================================================================ блоки */

const SEARCH_PARAMS = [
  { k: 'region', label: 'Где искать', t: 'region', d: '' },
  { k: 'find_all', label: 'Что искать', t: 'sel', d: 'first',
    opts: [['first', 'Только лучшее совпадение'], ['all', 'Все совпадения']] },
  { k: 'mode', label: 'Режим', t: 'sel', d: 'once',
    opts: [['once', 'Проверить один раз'], ['wait', 'Ждать, пока появится']] },
  { k: 'timeout', label: 'Ждать максимум, сек (0 — бесконечно)', t: 'num', d: 0, showIf: ['mode', 'wait'] },
  { k: 'interval', label: 'Пауза между попытками, сек', t: 'num', d: 0.7, step: 0.1, showIf: ['mode', 'wait'] },
];

const BLOCKS = {
  find_image: { icon: '🔍', title: 'Найти картинку', g: 'search', params: [
    { k: 'template', label: 'Картинка-образец', t: 'tpl' },
    { k: 'threshold', label: 'Точность совпадения (0.5–0.99)', t: 'num', d: 0.85, step: 0.01 },
    ...SEARCH_PARAMS,
  ]},
  find_object: { icon: '🧠', title: 'Найти объект (нейросеть)', g: 'search', params: [
    { k: 'model', label: 'Модель', t: 'model' },
    { k: 'class_name', label: 'Класс (пусто — любой)', t: 'text', d: '' },
    { k: 'conf', label: 'Уверенность (0.3–0.95)', t: 'num', d: 0.6, step: 0.05 },
    ...SEARCH_PARAMS,
  ]},
  ocr_read: { icon: '🔤', title: 'Прочитать текст (OCR)', g: 'search', params: [
    { k: 'region', label: 'Область экрана', t: 'region', d: '' },
    { k: 'digits', label: 'Распознавать', t: 'sel', d: 'no',
      opts: [['no', 'Любой текст'], ['yes', 'Только числа']] },
    { k: 'var', label: 'В какой счётчик записать', t: 'text', d: 'текст' },
  ]},
  click: { icon: '🖱', title: 'Клик', g: 'act', params: [
    { k: 'target', label: 'Куда', t: 'sel', d: 'found',
      opts: [['found', 'По найденному'], ['coords', 'По координатам']] },
    { k: 'x', label: 'X', t: 'num', d: 0, showIf: ['target', 'coords'] },
    { k: 'y', label: 'Y', t: 'num', d: 0, showIf: ['target', 'coords'] },
    { k: 'button', label: 'Кнопка', t: 'sel', d: 'left',
      opts: [['left', 'Левая'], ['double', 'Двойной клик'], ['right', 'Правая']] },
    { k: 'jitter', label: 'Разброс точки, пикс (0 — точно)', t: 'num', d: 0 },
  ]},
  drag_mouse: { icon: '✊', title: 'Перетащить мышью', g: 'act', params: [
    { k: 'target', label: 'Откуда', t: 'sel', d: 'found',
      opts: [['found', 'От найденного'], ['coords', 'От координат']] },
    { k: 'x', label: 'X', t: 'num', d: 0, showIf: ['target', 'coords'] },
    { k: 'y', label: 'Y', t: 'num', d: 0, showIf: ['target', 'coords'] },
    { k: 'x2', label: 'Куда X', t: 'num', d: 0 },
    { k: 'y2', label: 'Куда Y', t: 'num', d: 0 },
    { k: 'duration', label: 'Время перетаскивания, сек', t: 'num', d: 0.5, step: 0.1 },
  ]},
  hold_key: { icon: '⌨️', title: 'Удерживать клавишу', g: 'act', params: [
    { k: 'keys', label: 'Клавиша (w, shift, space…)', t: 'text', d: 'w' },
    { k: 'seconds', label: 'Сколько секунд держать', t: 'num', d: 1, step: 0.1 },
  ]},
  move_mouse: { icon: '🖱', title: 'Передвинуть мышь', g: 'act', params: [
    { k: 'target', label: 'Куда', t: 'sel', d: 'found',
      opts: [['found', 'К найденному'], ['coords', 'По координатам']] },
    { k: 'x', label: 'X', t: 'num', d: 0, showIf: ['target', 'coords'] },
    { k: 'y', label: 'Y', t: 'num', d: 0, showIf: ['target', 'coords'] },
    { k: 'duration', label: 'Время движения, сек', t: 'num', d: 0.3, step: 0.1 },
  ]},
  type_text: { icon: '⌨️', title: 'Ввести текст', g: 'act', params: [
    { k: 'text', label: 'Текст', t: 'text', d: '' },
  ]},
  press_key: { icon: '⌨️', title: 'Нажать клавишу', g: 'act', params: [
    { k: 'keys', label: 'Клавиша (enter, esc, f5, ctrl+c…)', t: 'text', d: 'enter' },
  ]},
  scroll: { icon: '🖲', title: 'Прокрутка', g: 'act', params: [
    { k: 'amount', label: 'Сила (>0 — вверх, <0 — вниз)', t: 'num', d: -500 },
  ]},
  wait: { icon: '⏳', title: 'Пауза', g: 'act', params: [
    { k: 'seconds', label: 'Секунды', t: 'num', d: 1, step: 0.1 },
    { k: 'rand', label: '± случайно, сек (0 — точно)', t: 'num', d: 0, step: 0.1 },
  ]},
  if_found: { icon: '🔀', title: 'Если найдено…', g: 'logic', params: [],
    zones: [['then', '✅ Если найдено'], ['els', '❌ Если не найдено']] },
  for_each: { icon: '📍', title: 'Для каждого найденного', g: 'logic', params: [],
    zones: [['children', 'Блоки (выполняются для каждой находки)']] },
  if_pixel: { icon: '🎨', title: 'Если цвет пикселя', g: 'logic', params: [
    { k: 'x', label: 'X (координаты снимка)', t: 'num', d: 0 },
    { k: 'y', label: 'Y', t: 'num', d: 0 },
    { k: 'color', label: 'Ожидаемый цвет', t: 'color', d: '#ff0000' },
    { k: 'tolerance', label: 'Допуск (0 — точь-в-точь, 30 — примерно)', t: 'num', d: 12 },
  ], zones: [['then', '✅ Цвет совпал'], ['els', '❌ Не совпал']] },
  counter: { icon: '🔢', title: 'Счётчик', g: 'logic', params: [
    { k: 'name', label: 'Имя счётчика', t: 'text', d: 'счётчик' },
    { k: 'action', label: 'Действие', t: 'sel', d: 'add',
      opts: [['add', 'Прибавить'], ['set', 'Установить']] },
    { k: 'value', label: 'Число', t: 'num', d: 1 },
  ]},
  if_var: { icon: '🧮', title: 'Если счётчик…', g: 'logic', params: [
    { k: 'name', label: 'Имя счётчика', t: 'text', d: 'счётчик' },
    { k: 'op', label: 'Сравнение', t: 'sel', d: '>=',
      opts: [['>=', '≥ (больше или равно)'], ['<=', '≤ (меньше или равно)'],
             ['>', '>'], ['<', '<'], ['==', '= (равно)'], ['!=', '≠ (не равно)']] },
    { k: 'value', label: 'С чем сравнить', t: 'num', d: 10 },
  ], zones: [['then', '✅ Верно'], ['els', '❌ Неверно']] },
  repeat: { icon: '🔁', title: 'Повторить N раз', g: 'logic',
    params: [{ k: 'count', label: 'Сколько раз', t: 'num', d: 3 }],
    zones: [['children', 'Блоки']] },
  loop_forever: { icon: '♾', title: 'Повторять бесконечно', g: 'logic', params: [],
    zones: [['children', 'Блоки']] },
  log: { icon: '💬', title: 'Сообщение в журнал', g: 'logic', params: [
    { k: 'message', label: 'Текст (можно {счётчик}, {найдено}, {время}, {текст})', t: 'text', d: '' },
  ]},
  stop: { icon: '⏹', title: 'Стоп', g: 'logic', params: [] },
  hud_show: { icon: '🖥', title: 'Показать на HUD', g: 'hud', params: [
    { k: 'line', label: 'Строка HUD (1–9)', t: 'num', d: 1 },
    { k: 'text', label: 'Текст (можно {счётчик}, {найдено}, {время}, {текст})', t: 'text', d: '' },
  ]},
  hud_clear: { icon: '🧹', title: 'Очистить HUD', g: 'hud', params: [
    { k: 'line', label: 'Строка (0 — убрать все)', t: 'num', d: 0 },
  ]},
  stats_write: { icon: '📊', title: 'Записать статистику', g: 'hud', params: [
    { k: 'note', label: 'Заметка (можно {счётчик}, {время}…)', t: 'text', d: '' },
  ]},
  signal: { icon: '🔔', title: 'Сигнал', g: 'hud', params: [
    { k: 'message', label: 'Сообщение (пикнет в браузере и на телефоне)', t: 'text', d: 'Нашёл!' },
  ]},
};

const GROUPS = [
  ['Поиск на экране', ['find_image', 'find_object', 'ocr_read']],
  ['Действия', ['click', 'move_mouse', 'drag_mouse', 'hold_key', 'type_text', 'press_key', 'scroll', 'wait']],
  ['Логика', ['if_found', 'for_each', 'if_pixel', 'if_var', 'counter', 'repeat', 'loop_forever', 'log', 'stop']],
  ['HUD и статистика', ['hud_show', 'hud_clear', 'stats_write', 'signal']],
];

/* Подробные объяснения блоков — открываются по «?» на блоке */
const HELP = {
  find_image: 'Делает снимок экрана и ищет на нём картинку-образец (кусочек экрана, сохранённый на вкладке «Экран и образцы»). Если нашёл — запоминает место, и следующие блоки «Клик», «Если найдено», «Для каждого» этим пользуются.\n\n«Точность» — насколько похожим должно быть совпадение: 0.85 обычно хорошо. Находит не то — повышай (0.9–0.95), не находит — слегка понижай.\n\n«Ждать, пока появится» — проверяет экран снова и снова, пока картинка не появится или не кончится время ожидания (0 — ждать вечно). Это и есть «если не нашёл — продолжай искать».\n\n«Где искать» — ограничить поиск сохранённой областью: быстрее и без ложных срабатываний. «Все совпадения» — найти каждый экземпляр, а не только лучший (нужно для «Для каждого найденного»).\n\nВажно: образец сравнивается точь-в-точь, поэтому после смены разрешения или масштаба игры сохрани его заново.',
  find_object: 'То же, что «Найти картинку», но ищет обученная нейросеть — она узнаёт объекты, которые каждый раз выглядят по-разному: другой цвет, форма, поворот, место.\n\nСначала обучи модель на вкладке «Обучение»: сделай 5–10 снимков игры, обведи объекты рамками (30–50 штук), нажми «Обучить».\n\n«Класс» — что именно искать, если в модели размечено несколько видов объектов; пусто — любой. «Уверенность» — насколько сеть должна быть уверена: находит лишнее → повышай, пропускает настоящее → понижай.\n\nНейропоиск медленнее поиска по картинке (секунды на проверку) — это нормально.',
  ocr_read: 'Читает текст или число из сохранённой области экрана — например, там, где в игре написано количество золота или здоровья.\n\nРезультат кладётся в счётчик с указанным именем: дальше его можно сравнивать («Если счётчик…»), показывать на HUD и вставлять в сообщения как {имя}.\n\nОбласть сохрани на вкладке «Экран» в режиме «Область» — там же кнопка «Прочитать текст» сразу покажет, что распознаётся. Для чисел включай «Только числа» — так надёжнее.\n\nНужна бесплатная программа Tesseract (как поставить — в README).',
  click: 'Кликает мышью. «По найденному» — в центр того, что нашёл последний блок «Найти…»; если ничего не найдено, клик пропускается с предупреждением в журнале.\n\n«По координатам» — в точную точку экрана. Координаты подскажет вкладка «Экран»: сделай снимок и коротко нажми на точку — появятся (X, Y) и цвет.\n\n«Разброс точки» — кликать каждый раз в чуть разное место (2–5 пикселей), выглядит естественнее.',
  move_mouse: 'Плавно передвигает курсор к найденному или к координатам, не кликая. Полезно навести мышь (чтобы в игре всплыла подсказка) или сделать движение перед кликом плавнее.',
  drag_mouse: 'Зажимает левую кнопку в начальной точке (найденное или координаты), тащит в точку «Куда» и отпускает. Для перетаскивания предметов в инвентаре, ползунков, перемещения карты.',
  hold_key: 'Зажимает клавишу на заданное время и отпускает — бег (w), прыжки (space), ускорение (shift). При остановке бота клавиша гарантированно отпускается.',
  type_text: 'Печатает текст, как будто с клавиатуры. Русский текст вставляется через буфер обмена. Работают подстановки: {счётчик}, {текст}, {время}, {найдено}.',
  press_key: 'Нажимает одну клавишу или сочетание. Названия по-английски: enter, esc, space, tab, f5, а сочетания через плюс: ctrl+c, alt+tab, ctrl+shift+s.',
  scroll: 'Крутит колесо мыши: положительное число — вверх, отрицательное — вниз. 500 — это несколько щелчков колеса; точное значение подбери опытным путём.',
  wait: 'Просто ждёт указанное время. «± случайно» добавляет разброс: пауза 1 ± 0.3 длится от 0.7 до 1.3 сек — так бот не действует как метроном.\n\nСтавь паузы после кликов, чтобы игра успевала отреагировать, и обязательно — внутри бесконечных циклов.',
  if_found: 'Смотрит на результат ПОСЛЕДНЕГО блока «Найти…»: если цель нашлась — выполняется ветка «да», если нет — ветка «нет». Ставь его сразу после блока поиска.\n\nВнутри веток могут быть любые блоки, в том числе другие условия и циклы. Любую ветку можно оставить пустой.',
  for_each: 'Работает в паре с поиском, где выбрано «Все совпадения»: выполняет вложенные блоки для каждой находки по очереди — первая цель, вторая, третья…\n\nВнутри блока «Клик по найденному» кликает по текущей цели.\n\nПример: Найти объект (все совпадения) → Для каждого: Клик → Счётчик +1 → Пауза 0.5.',
  if_pixel: 'Проверяет цвет одной точки экрана: полоска здоровья ещё красная? лампочка загорелась?\n\nКоординаты и цвет возьми на вкладке «Экран»: сделай снимок и коротко нажми на нужную точку — появятся (X, Y) и код цвета, вбей их сюда.\n\n«Допуск» — насколько цвет может отличаться: 12 — почти точное совпадение, 30 — заметные отличия (освещение в игре меняется).',
  counter: 'Счётчики — память бота. «Прибавить» увеличивает (отрицательное число уменьшит), «Установить» задаёт точное значение.\n\nЗначение можно показать где угодно подстановкой {имя_счётчика}: в сообщении, на HUD, в статистике.\n\nПример: считать собранные камни и остановиться на 50 (вместе с блоком «Если счётчик…»).',
  if_var: 'Сравнивает счётчик с числом: верно — ветка «да», нет — ветка «нет».\n\nВместе с «Повторять бесконечно» получается «работай, пока не…»: цикл → действия → счётчик +1 → Если счётчик ≥ 50 → Стоп.\n\nРаботает и со значением из «Прочитать текст», если там распозналось число.',
  repeat: 'Выполняет вложенные блоки указанное число раз подряд.',
  loop_forever: 'Выполняет вложенные блоки по кругу, пока бота не остановят (кнопка «Стоп», горячая клавиша, мышь в левый верхний угол) или пока не сработает блок «Стоп».\n\nГлавный блок для ботов вида «работай, пока не выключу». Внутри обязательно нужна пауза или поиск с ожиданием — иначе цикл будет крутиться впустую тысячи раз подряд.',
  log: 'Пишет строку в журнал. Удобно для отладки — видно, до какого места дошёл сценарий. Подстановки работают: «Собрано {камни} на {время}».',
  stop: 'Полностью останавливает сценарий. Обычно ставится внутри условия: «Если счётчик ≥ 50 → Стоп».',
  hud_show: 'Показывает строку поверх игры — в полупрозрачном окошке в правом верхнем углу экрана (клики проходят сквозь него) — и зелёной панелью в браузере, в том числе на телефоне.\n\nСтрок до девяти, каждая обновляется отдельно по своему номеру. Подстановки работают: «Камней: {камни}».\n\nОбновляй HUD внутри цикла — и всегда будешь видеть свежие цифры.',
  hud_clear: 'Убирает с HUD строку с указанным номером, а 0 — очищает весь HUD.',
  stats_write: 'Добавляет строку в таблицу на вкладке «Статистика» (и в файл data/stats.csv): время, сценарий, заметка и все счётчики.\n\nВызывай в конце круга цикла или по событию — получится история работы бота. CSV можно скачать и открыть в Excel.',
  signal: 'Издаёт звуковой сигнал и показывает уведомление во всех открытых вкладках конструктора — на ПК и на телефоне. «Нашёл редкую вещь», «кончилась энергия» — ты услышишь.\n\nПравило браузеров: звук включается после того, как ты хотя бы раз нажал что-нибудь на странице.',
};

let scenario = { name: 'Мой бот', blocks: [] };
let templates = [];
let modelsList = [];
let regions = {};  // именованные области экрана {имя: {x,y,w,h}}
let listsLoaded = { tpl: false, model: false, region: false };
let caps = {};     // возможности сервера (что установлено)

/* ================================================================ проверка логики */

let problemsMap = new Map();   // блок → [{level: 'err'|'warn', msg}]
let blockToEl = new Map();     // блок → его карточка в DOM

function validateScenario() {
  problemsMap = new Map();
  const add = (b, level, msg) => {
    if (!problemsMap.has(b)) problemsMap.set(b, []);
    problemsMap.get(b).push({ level, msg });
  };
  const st = { hasSearch: false, lastFindAll: false, counters: new Set() };
  vWalkList(scenario.blocks, st, add);
  return problemsMap;
}

function vBranch(st) {
  return { hasSearch: st.hasSearch, lastFindAll: st.lastFindAll, counters: new Set(st.counters) };
}

function vMerge(st, ...branches) {
  branches.forEach(b2 => {
    if (b2.hasSearch && !st.hasSearch) { st.hasSearch = true; st.lastFindAll = b2.lastFindAll; }
    b2.counters.forEach(c => st.counters.add(c));
  });
}

function vHasDelay(arr) {
  return (arr || []).some(b =>
    b.type === 'wait' || b.type === 'hold_key' ||
    ((b.type === 'find_image' || b.type === 'find_object') && (b.params || {}).mode === 'wait') ||
    vHasDelay(b.children) || vHasDelay(b.then) || vHasDelay(b.els));
}

function vRegionCheck(b, p, add, required) {
  const r = (p.region || '').trim();
  if (!r) {
    if (required) add(b, 'err', 'Не выбрана область. Сохрани место на экране: вкладка «Экран», режим «Область».');
    return;
  }
  if (listsLoaded.region && !(r in regions)) {
    add(b, 'err', `Область «${r}» не найдена среди сохранённых — возможно, её удалили.`);
  }
}

function vWalkList(arr, st, add) {
  (arr || []).forEach(b => vWalkBlock(b, st, add));
}

function vWalkBlock(b, st, add) {
  const p = b.params || {};
  switch (b.type) {
    case 'find_image': {
      if (!p.template) add(b, 'err', 'Не выбран образец. Сохрани кусочек экрана (вкладка «Экран», режим «Образец») и выбери его в списке.');
      else if (listsLoaded.tpl && !templates.includes(p.template)) add(b, 'err', `Образец «${p.template}» не найден среди сохранённых — возможно, его удалили.`);
      vRegionCheck(b, p, add, false);
      st.hasSearch = true;
      st.lastFindAll = p.find_all === 'all';
      break;
    }
    case 'find_object': {
      if (!p.model) add(b, 'err', 'Не выбрана модель. Обучи нейросеть на вкладке «Обучение» и выбери её в списке.');
      else if (listsLoaded.model && !modelsList.some(m => m.name === p.model)) add(b, 'err', `Модель «${p.model}» не найдена — возможно, её удалили.`);
      else if (p.class_name && listsLoaded.model) {
        const m = modelsList.find(mm => mm.name === p.model);
        if (m && m.classes && !m.classes.includes(String(p.class_name).trim())) {
          add(b, 'warn', `В модели «${p.model}» нет класса «${p.class_name}» (есть: ${m.classes.join(', ')}).`);
        }
      }
      vRegionCheck(b, p, add, false);
      st.hasSearch = true;
      st.lastFindAll = p.find_all === 'all';
      break;
    }
    case 'ocr_read': {
      vRegionCheck(b, p, add, true);
      const v = String(p.var || '').trim() || 'текст';
      st.counters.add(v);
      break;
    }
    case 'click':
    case 'move_mouse':
    case 'drag_mouse': {
      if ((p.target ?? 'found') === 'found' && !st.hasSearch) {
        add(b, 'err', 'Выше нет ни одного блока «Найти…» — цели не будет. Добавь поиск перед этим блоком или переключи на «По координатам».');
      }
      break;
    }
    case 'press_key':
    case 'hold_key': {
      if (!String(p.keys || '').trim()) add(b, 'err', 'Не указана клавиша.');
      break;
    }
    case 'type_text': {
      if (!String(p.text || '')) add(b, 'warn', 'Текст пуст — блок ничего не введёт.');
      break;
    }
    case 'if_found': {
      if (!st.hasSearch) add(b, 'warn', 'Выше нет блока «Найти…» — условие всегда пойдёт в ветку «нет».');
      if (!(b.then || []).length && !(b.els || []).length) add(b, 'warn', 'Обе ветки пусты — блок ничего не делает. Добавь блоки внутрь.');
      const s1 = vBranch(st), s2 = vBranch(st);
      vWalkList(b.then, s1, add);
      vWalkList(b.els, s2, add);
      vMerge(st, s1, s2);
      return;
    }
    case 'if_pixel': {
      if (!(b.then || []).length && !(b.els || []).length) add(b, 'warn', 'Обе ветки пусты — блок ничего не делает. Добавь блоки внутрь.');
      const s1 = vBranch(st), s2 = vBranch(st);
      vWalkList(b.then, s1, add);
      vWalkList(b.els, s2, add);
      vMerge(st, s1, s2);
      return;
    }
    case 'if_var': {
      const name = String(p.name || '').trim();
      if (!name) add(b, 'err', 'Не указано имя счётчика.');
      else if (!st.counters.has(name)) add(b, 'warn', `Счётчик «${name}» выше нигде не создавался («Счётчик» или «Прочитать текст») — сравнение будет с нулём.`);
      if (!(b.then || []).length && !(b.els || []).length) add(b, 'warn', 'Обе ветки пусты — блок ничего не делает. Добавь блоки внутрь.');
      const s1 = vBranch(st), s2 = vBranch(st);
      vWalkList(b.then, s1, add);
      vWalkList(b.els, s2, add);
      vMerge(st, s1, s2);
      return;
    }
    case 'for_each': {
      if (!st.hasSearch) add(b, 'err', 'Выше нет блока «Найти…» — списка целей не будет. Добавь поиск и выбери в нём «Все совпадения».');
      else if (!st.lastFindAll) add(b, 'warn', 'Последний поиск выше ищет «только лучшее совпадение» — «Для каждого» получит максимум одну цель. В блоке поиска выбери «Все совпадения».');
      if (!(b.children || []).length) add(b, 'warn', 'Внутри нет блоков — цикл ничего не сделает.');
      const s1 = vBranch(st);
      s1.hasSearch = true;  // внутри цикла цель точно есть
      vWalkList(b.children, s1, add);
      vMerge(st, s1);
      return;
    }
    case 'repeat':
    case 'loop_forever': {
      if (!(b.children || []).length) add(b, 'warn', 'Внутри нет блоков.');
      if (b.type === 'loop_forever' && (b.children || []).length && !vHasDelay(b.children)) {
        add(b, 'warn', 'В бесконечном цикле нет ни «Паузы», ни поиска с ожиданием — он будет крутиться тысячи раз подряд. Добавь «Паузу».');
      }
      const s1 = vBranch(st);
      vWalkList(b.children, s1, add);
      vMerge(st, s1);
      return;
    }
    case 'counter': {
      const name = String(p.name || '').trim();
      if (!name) add(b, 'err', 'Не указано имя счётчика.');
      else st.counters.add(name);
      break;
    }
    case 'hud_show': {
      if (!String(p.text || '').trim()) add(b, 'warn', 'Текст пуст — на HUD будет пустая строка.');
      break;
    }
    default:
      break;
  }
}

function applyProblems(card, list) {
  card.classList.remove('invalid', 'warnb');
  let box = card.querySelector(':scope > .block-problems');
  if (!list || !list.length) {
    if (box) box.remove();
    return;
  }
  const hasErr = list.some(x => x.level === 'err');
  card.classList.add(hasErr ? 'invalid' : 'warnb');
  if (!box) {
    box = el('div', 'block-problems');
    const head = card.querySelector(':scope > .block-head');
    head.insertAdjacentElement('afterend', box);
  }
  box.innerHTML = '';
  list.forEach(x => box.appendChild(
    el('div', x.level === 'err' ? 'perr' : 'pwarn',
       (x.level === 'err' ? '⛔ ' : '⚠ ') + x.msg)));
}

function updateProblemChip() {
  let errs = 0, warns = 0;
  problemsMap.forEach(list => list.forEach(x => (x.level === 'err' ? errs++ : warns++)));
  const chip = $('#problemChip');
  if (!scenario.blocks.length) return chip.classList.add('hidden');
  chip.classList.remove('hidden');
  if (errs) {
    chip.textContent = `⛔ Ошибок: ${errs}` + (warns ? ` · предупреждений: ${warns}` : '');
    chip.className = 'chip err';
  } else if (warns) {
    chip.textContent = `⚠ Предупреждений: ${warns}`;
    chip.className = 'chip warn';
  } else {
    chip.textContent = '✅ Логика в порядке';
    chip.className = 'chip ok';
  }
}

let validateTimer = null;
function scheduleValidate() {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(() => {
    validateScenario();
    blockToEl.forEach((cardEl, b) => applyProblems(cardEl, problemsMap.get(b)));
    updateProblemChip();
  }, 250);
}

function newBlock(type) {
  const def = BLOCKS[type];
  const b = { type, params: {} };
  (def.params || []).forEach(p => { if (p.d !== undefined) b.params[p.k] = p.d; });
  (def.zones || []).forEach(([key]) => { b[key] = []; });
  return b;
}

function paramField(b, p, rerender) {
  if (p.showIf && (b.params[p.showIf[0]] ?? '') !== p.showIf[1]) return null;
  const wrap = el('div', 'param');
  const lab = el('label', null, p.label);
  wrap.appendChild(lab);
  let inp;
  if (p.t === 'sel' || p.t === 'tpl' || p.t === 'model' || p.t === 'region') {
    inp = document.createElement('select');
    let opts;
    if (p.t === 'sel') opts = p.opts;
    else if (p.t === 'tpl') opts = templates.map(t => [t, t]);
    else if (p.t === 'region') opts = Object.keys(regions).map(r => [r, r]);
    else opts = modelsList.map(m => [m.name, m.name]);
    if (p.t === 'tpl') {
      opts = [['', opts.length ? '— выбери образец —' : 'нет образцов (вкладка «Экран»)'], ...opts];
    } else if (p.t === 'model') {
      opts = [['', opts.length ? '— выбери модель —' : 'нет моделей (вкладка «Обучение»)'], ...opts];
    } else if (p.t === 'region') {
      opts = [['', 'Весь экран'], ...opts];
    }
    const cur = b.params[p.k] ?? p.d ?? '';
    if (cur && !opts.some(o => o[0] === cur)) opts.push([cur, cur + ' (нет файла)']);
    opts.forEach(([v, t]) => {
      const o = document.createElement('option');
      o.value = v; o.textContent = t;
      inp.appendChild(o);
    });
    inp.value = cur;
    inp.onchange = () => { b.params[p.k] = inp.value; rerender(); };
  } else if (p.t === 'num') {
    inp = document.createElement('input');
    inp.type = 'number';
    if (p.step) inp.step = p.step;
    inp.value = b.params[p.k] ?? p.d ?? 0;
    inp.oninput = () => { b.params[p.k] = parseFloat(inp.value) || 0; scheduleValidate(); };
  } else if (p.t === 'color') {
    inp = document.createElement('input');
    inp.type = 'color';
    inp.value = b.params[p.k] ?? p.d ?? '#ff0000';
    inp.oninput = () => { b.params[p.k] = inp.value; scheduleValidate(); };
  } else {
    inp = document.createElement('input');
    inp.type = 'text';
    inp.value = b.params[p.k] ?? p.d ?? '';
    inp.oninput = () => { b.params[p.k] = inp.value; scheduleValidate(); };
  }
  wrap.appendChild(inp);
  return wrap;
}

function renderBlocks() {
  const root = $('#blocks');
  root.innerHTML = '';
  blockToEl = new Map();
  validateScenario();
  renderList(scenario.blocks, root);
  updateProblemChip();
}

function renderList(arr, parentEl) {
  arr.forEach((b, i) => parentEl.appendChild(renderBlock(b, arr, i)));
  const add = el('button', 'add-btn', '＋ Добавить блок');
  add.onclick = () => openMenu(type => { arr.push(newBlock(type)); renderBlocks(); });
  parentEl.appendChild(add);
}

function renderBlock(b, arr, i) {
  const def = BLOCKS[b.type] || { icon: '❓', title: b.type, params: [] };
  const card = el('div', 'block g-' + (def.g || 'search'));
  blockToEl.set(b, card);

  const head = el('div', 'block-head');
  head.appendChild(el('span', null, def.icon));
  head.appendChild(el('span', 'block-title', def.title));
  const ctl = el('div', 'block-ctl');
  const mk = (txt, title, fn) => {
    const btn = el('button', null, txt);
    btn.title = title;
    btn.onclick = fn;
    ctl.appendChild(btn);
  };
  mk('?', 'Как работает этот блок', () => showBlockHelp(b.type));
  mk('↑', 'Выше', () => { if (i > 0) { [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]]; renderBlocks(); } });
  mk('↓', 'Ниже', () => { if (i < arr.length - 1) { [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]]; renderBlocks(); } });
  mk('⧉', 'Дублировать', () => { arr.splice(i + 1, 0, JSON.parse(JSON.stringify(b))); renderBlocks(); });
  mk('✕', 'Удалить', () => { arr.splice(i, 1); renderBlocks(); });
  head.appendChild(ctl);
  card.appendChild(head);
  applyProblems(card, problemsMap.get(b));

  if ((def.params || []).length) {
    const pg = el('div', 'params');
    def.params.forEach(p => {
      const f = paramField(b, p, renderBlocks);
      if (f) pg.appendChild(f);
    });
    card.appendChild(pg);
  }
  (def.zones || []).forEach(([key, label]) => {
    if (!b[key]) b[key] = [];
    const z = el('div', 'zone');
    z.appendChild(el('div', 'zone-label', label));
    renderList(b[key], z);
    card.appendChild(z);
  });
  return card;
}

/* ---------------- модальное окно (справка, настройки) ---------------- */

function openModal(title, build) {
  const panel = $('#modalPanel');
  panel.innerHTML = '';
  const head = el('div', 'modal-head');
  head.appendChild(el('b', null, title));
  const close = el('button', 'btn', '✕');
  close.onclick = closeModal;
  head.appendChild(close);
  panel.appendChild(head);
  build(panel);
  $('#modal').classList.remove('hidden');
}
function closeModal() { $('#modal').classList.add('hidden'); }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

function helpNodes(type, panel) {
  (HELP[type] || 'Описания пока нет.').split('\n\n').forEach(par =>
    panel.appendChild(el('p', 'help-p', par)));
}

function showBlockHelp(type) {
  const def = BLOCKS[type] || { icon: '❓', title: type };
  openModal(def.icon + ' ' + def.title, panel => helpNodes(type, panel));
}

$('#btnBlocksHelp').onclick = () => {
  openModal('❓ Справка по блокам', panel => {
    panel.appendChild(el('p', 'help-p',
      'Бот выполняет блоки сверху вниз. Блоки «Найти…» запоминают цель, блоки действий ею пользуются, блоки логики решают, что делать дальше. Такой же значок «?» есть на каждом блоке в сценарии.'));
    GROUPS.forEach(([g, types]) => {
      panel.appendChild(el('div', 'menu-group', g));
      types.forEach(t => {
        panel.appendChild(el('h4', 'help-h', BLOCKS[t].icon + ' ' + BLOCKS[t].title));
        helpNodes(t, panel);
      });
    });
  });
};

/* ---------------- горячие клавиши ---------------- */

const HOTKEY_ACTIONS = [
  ['toggle', '▶⏹ Запустить последний сценарий / остановить'],
  ['stop', '⏹ Аварийная остановка'],
  ['shot_label', '📸 Снимок из игры → разметка (обучение нейросети)'],
  ['shot_region', '📸 Снимок из игры → область или образец'],
];
const HOTKEY_CHOICES = ['off', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12'];

$('#btnHotkeys').onclick = async () => {
  let settings;
  try { settings = await api('/api/settings'); }
  catch (e) { return toast(e.message, true); }
  openModal('⌨ Горячие клавиши', panel => {
    if (caps.hotkeys === false) {
      panel.appendChild(el('p', 'help-p pwarn',
        '⚠ На сервере не установлен pynput — клавиши не будут срабатывать. Выполни на ПК: pip install pynput и перезапусти программу.'));
    }
    panel.appendChild(el('p', 'help-p',
      'Клавиши работают глобально — даже когда открыта игра, а не браузер. «Снимок из игры» замораживает экран и сразу открывает его здесь для разметки или выделения области.'));
    const selects = {};
    HOTKEY_ACTIONS.forEach(([action, label]) => {
      const row = el('div', 'hk-row');
      row.appendChild(el('span', 'hk-label', label));
      const sel2 = document.createElement('select');
      HOTKEY_CHOICES.forEach(k => sel2.appendChild(
        new Option(k === 'off' ? 'выкл' : k.toUpperCase(), k)));
      sel2.value = settings.hotkeys[action] || 'off';
      selects[action] = sel2;
      row.appendChild(sel2);
      panel.appendChild(row);
    });
    const save = el('button', 'btn primary', '💾 Сохранить');
    save.onclick = async () => {
      const hk = {};
      Object.entries(selects).forEach(([a, s]) => { hk[a] = s.value; });
      try {
        await api('/api/settings', { method: 'POST', body: JSON.stringify({ hotkeys: hk }) });
        toast('⌨ Клавиши сохранены');
        closeModal();
      } catch (e) { toast(e.message, true); }
    };
    panel.appendChild(el('div', 'row')).appendChild(save);
  });
};

function openMenu(cb) {
  const panel = $('#menuPanel');
  panel.innerHTML = '';
  panel.appendChild(el('div', 'menu-group', 'Выбери блок'));
  GROUPS.forEach(([g, types]) => {
    panel.appendChild(el('div', 'menu-group', g));
    types.forEach(t => {
      const it = el('button', 'menu-item', BLOCKS[t].icon + '  ' + BLOCKS[t].title);
      it.onclick = () => { closeMenu(); cb(t); };
      panel.appendChild(it);
    });
  });
  $('#menu').classList.remove('hidden');
}
function closeMenu() { $('#menu').classList.add('hidden'); }
$('#menu').addEventListener('click', e => { if (e.target.id === 'menu') closeMenu(); });

/* ---------------- сценарии: сохранить / открыть ---------------- */

async function refreshScenarioList() {
  const names = await api('/api/scenarios');
  const sel = $('#scList');
  sel.innerHTML = '';
  if (!names.length) sel.appendChild(new Option('— нет сохранённых —', ''));
  names.forEach(n => sel.appendChild(new Option(n, n)));
}

$('#scName').oninput = () => { scenario.name = $('#scName').value; };

$('#btnSave').onclick = async () => {
  const name = $('#scName').value.trim();
  if (!name) return toast('Дай сценарию имя', true);
  try {
    scenario.name = name;
    await api('/api/scenarios/' + encodeURIComponent(name),
      { method: 'POST', body: JSON.stringify({ scenario }) });
    toast('💾 Сценарий сохранён');
    refreshScenarioList();
  } catch (e) { toast(e.message, true); }
};

$('#btnLoad').onclick = async () => {
  const name = $('#scList').value;
  if (!name) return toast('Нет сохранённых сценариев', true);
  try {
    scenario = await api('/api/scenarios/' + encodeURIComponent(name));
    scenario.blocks = scenario.blocks || [];
    $('#scName').value = scenario.name || name;
    renderBlocks();
    toast('📂 Открыт «' + name + '»');
  } catch (e) { toast(e.message, true); }
};

$('#btnDelSc').onclick = async () => {
  const name = $('#scList').value;
  if (!name || !confirm('Удалить сценарий «' + name + '»?')) return;
  await api('/api/scenarios/' + encodeURIComponent(name), { method: 'DELETE' });
  refreshScenarioList();
  toast('Удалён');
};

$('#btnNew').onclick = () => {
  if (scenario.blocks.length && !confirm('Начать новый сценарий? Несохранённые блоки пропадут.')) return;
  scenario = { name: 'Мой бот', blocks: [] };
  $('#scName').value = scenario.name;
  renderBlocks();
};

$('#btnExport').onclick = () => {
  scenario.name = $('#scName').value.trim() || 'Мой бот';
  const blob = new Blob([JSON.stringify(scenario, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = scenario.name + '.json';
  a.click();
  URL.revokeObjectURL(a.href);
};

$('#btnImport').onclick = () => $('#importFile').click();
$('#importFile').onchange = async () => {
  const f = $('#importFile').files[0];
  $('#importFile').value = '';
  if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    if (!Array.isArray(data.blocks)) throw new Error('В файле нет блоков сценария');
    if (scenario.blocks.length && !confirm('Заменить текущие блоки сценарием из файла?')) return;
    scenario = { name: data.name || f.name.replace(/\.json$/i, ''), blocks: data.blocks };
    $('#scName').value = scenario.name;
    renderBlocks();
    toast('⬆ Сценарий загружен из файла');
  } catch (e) { toast('Не получилось: ' + e.message, true); }
};

$('#btnExample').onclick = () => {
  if (scenario.blocks.length && !confirm('Заменить текущие блоки примером?')) return;
  scenario = { name: 'Пример: найти и кликнуть', blocks: [
    { type: 'log', params: { message: 'Привет! Ищу картинку и кликаю по ней' } },
    { type: 'loop_forever', params: {}, children: [
      { type: 'find_image', params: { template: '', threshold: 0.85, mode: 'wait', timeout: 10, interval: 1 } },
      { type: 'if_found', params: {}, then: [
        { type: 'click', params: { target: 'found', button: 'left' } },
        { type: 'wait', params: { seconds: 2 } },
      ], els: [
        { type: 'log', params: { message: 'Не нашёл за 10 сек, пробую снова…' } },
      ]},
    ]},
  ]};
  $('#scName').value = scenario.name;
  renderBlocks();
  toast('✨ Пример загружен — выбери образец в блоке «Найти картинку»');
};

/* ---------------- запуск и статус ---------------- */

$('#btnRun').onclick = async () => {
  validateScenario();
  let errs = 0;
  problemsMap.forEach(list => list.forEach(x => { if (x.level === 'err') errs++; }));
  if (errs && !confirm(`В сценарии ${errs} ошибк${errs === 1 ? 'а' : 'и'} (подсвечены красным в редакторе). Запустить всё равно?`)) {
    switchTab('editor');
    renderBlocks();
    return;
  }
  try {
    scenario.name = $('#scName').value.trim() || 'Мой бот';
    await api('/api/run', { method: 'POST', body: JSON.stringify({ scenario }) });
    toast('▶ Бот запущен');
    switchTab('log');
  } catch (e) { toast(e.message, true); }
};

$('#btnStop').onclick = () => api('/api/stop', { method: 'POST' }).catch(() => {});

let prevTrainRunning = false;
let capsShown = false;

async function refreshStatus() {
  let st;
  try { st = await api('/api/status'); } catch (e) { return; }

  const chip = $('#statusChip');
  if (st.running) {
    chip.textContent = '🟢 Работает: ' + st.scenario;
    chip.className = 'chip run';
  } else {
    chip.textContent = '⚪ Остановлен';
    chip.className = 'chip';
  }
  $('#btnRun').disabled = st.running;
  $('#btnStop').disabled = !st.running;

  const tr = st.training || {};
  const prog = $('#trainProg');
  if (tr.running) {
    prog.classList.remove('hidden');
    const pct = tr.epochs ? Math.round(100 * tr.epoch / tr.epochs) : 0;
    $('#trainBar').style.width = pct + '%';
    $('#trainInfo').textContent = tr.epoch
      ? `Эпоха ${tr.epoch}/${tr.epochs} · ошибка ${tr.loss ?? '—'} · точность ${tr.acc != null ? Math.round(tr.acc * 100) + '%' : '—'}`
      : 'Готовлю примеры…';
    $('#btnTrain').disabled = true;
  } else {
    prog.classList.add('hidden');
    $('#btnTrain').disabled = false;
    if (prevTrainRunning) {  // обучение только что закончилось
      refreshModels().then(renderBlocks).catch(() => {});
      if (tr.done) toast('✅ Модель обучена');
    }
  }
  prevTrainRunning = !!tr.running;

  caps = st.capabilities || caps;
  if (!capsShown && st.capabilities) {
    capsShown = true;
    const c = st.capabilities;
    const warn = $('#capWarn');
    const add = (txt) => { const s = el('span', 'chip warn', txt); warn.appendChild(s); };
    if (!c.screen) add('⚠ Снимки экрана: pip install mss numpy');
    if (!c.opencv) add('⚠ Поиск по картинке: pip install opencv-python');
    if (!c.mouse) add('⚠ Мышь и клавиатура: pip install pyautogui');
    if (!c.neural) add('ℹ Нейросеть: pip install torch');
    if (!c.ocr) add('ℹ Чтение текста: установи Tesseract (см. README)');
    if (!c.hotkeys) add('ℹ Горячие клавиши: pip install pynput');
  }
}

/* ---------------- вкладки ---------------- */

function switchTab(name) {
  document.querySelectorAll('#tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach(s =>
    s.classList.toggle('active', s.id === 'tab-' + name));
  if (name === 'stats') refreshStats().catch(() => {});
}
document.querySelectorAll('#tabs button').forEach(b =>
  b.onclick = () => switchTab(b.dataset.tab));

/* ---------------- экран: снимок, образцы, разметка ---------------- */

const cv = $('#cv');
const ctx = cv.getContext('2d');
let cvImg = null;        // текущая картинка на канвасе
let boxes = [];          // рамки разметки [{x,y,w,h,cls}]
let sel = null;          // выделение для образца {x,y,w,h}
let drawing = null;      // рисуемый прямоугольник
let mode = 'sample';     // sample | label
let editingId = null;    // id снимка из датасета, который правим
let liveTimer = null;

function loadFrame(url, cb) {
  const img = new Image();
  img.onload = () => {
    cvImg = img;
    cv.width = img.naturalWidth;
    cv.height = img.naturalHeight;
    cv.classList.remove('hidden');
    draw();
    if (cb) cb();
  };
  img.onerror = () => toast('Не удалось загрузить снимок', true);
  img.src = url;
}

function draw() {
  if (!cvImg) return;
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.drawImage(cvImg, 0, 0);
  if (liveOn()) return;  // в живом просмотре рамки не показываем
  const lw = Math.max(2, Math.round(cv.width / 640));
  ctx.font = (7 * lw) + 'px sans-serif';
  boxes.forEach(b => {
    ctx.strokeStyle = '#3ecf8e';
    ctx.lineWidth = lw;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    const label = b.cls;
    const tw = ctx.measureText(label).width + 8;
    ctx.fillStyle = 'rgba(62,207,142,.85)';
    ctx.fillRect(b.x, Math.max(0, b.y - 9 * lw), tw, 9 * lw);
    ctx.fillStyle = '#04120b';
    ctx.fillText(label, b.x + 4, Math.max(7 * lw, b.y - 2 * lw));
  });
  if (mode === 'region') {  // показываем уже сохранённые области
    ctx.strokeStyle = '#b07cff';
    ctx.lineWidth = lw;
    ctx.font = (7 * lw) + 'px sans-serif';
    Object.entries(regions).forEach(([name, r]) => {
      ctx.strokeRect(r.x, r.y, r.w, r.h);
      ctx.fillStyle = 'rgba(176,124,255,.85)';
      const tw = ctx.measureText(name).width + 8;
      ctx.fillRect(r.x, Math.max(0, r.y - 9 * lw), tw, 9 * lw);
      ctx.fillStyle = '#160b24';
      ctx.fillText(name, r.x + 4, Math.max(7 * lw, r.y - 2 * lw));
    });
  }
  const r = drawing ? normRect(drawing) : sel;
  if (r) {
    ctx.strokeStyle = '#4f8cff';
    ctx.lineWidth = lw;
    ctx.setLineDash([6 * lw, 4 * lw]);
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.setLineDash([]);
  }
}

function toImg(e) {
  const rect = cv.getBoundingClientRect();
  const k = cv.width / rect.width;
  return {
    x: Math.max(0, Math.min(cv.width, (e.clientX - rect.left) * k)),
    y: Math.max(0, Math.min(cv.height, (e.clientY - rect.top) * k)),
  };
}
function normRect(d) {
  return {
    x: Math.round(Math.min(d.x0, d.x)), y: Math.round(Math.min(d.y0, d.y)),
    w: Math.round(Math.abs(d.x - d.x0)), h: Math.round(Math.abs(d.y - d.y0)),
  };
}

cv.addEventListener('pointerdown', e => {
  if (liveOn() || !cvImg) return;
  const p = toImg(e);
  drawing = { x0: p.x, y0: p.y, x: p.x, y: p.y };
  cv.setPointerCapture(e.pointerId);
  e.preventDefault();
});
cv.addEventListener('pointermove', e => {
  if (!drawing) return;
  const p = toImg(e);
  drawing.x = p.x; drawing.y = p.y;
  draw();
});
cv.addEventListener('pointerup', e => {
  if (!drawing) return;
  const r = normRect(drawing);
  drawing = null;
  if (r.w < 6 || r.h < 6) {
    // короткое нажатие: в разметке — удалить рамку, иначе — показать пиксель
    if (mode === 'label') {
      const p = toImg(e);
      const idx = boxes.findLastIndex(b =>
        p.x >= b.x && p.x <= b.x + b.w && p.y >= b.y && p.y <= b.y + b.h);
      if (idx >= 0) boxes.splice(idx, 1);
    } else {
      sel = null;
      showPixelInfo(toImg(e));
    }
  } else if (mode === 'label') {
    boxes.push({ ...r, cls: ($('#clsName').value.trim() || 'объект') });
  } else {
    sel = r;  // образец или область
  }
  draw();
});

async function showPixelInfo(p) {
  if (editingId) return;  // точный цвет читается только с замороженного снимка
  try {
    const info = await api(`/api/pixel?x=${Math.round(p.x)}&y=${Math.round(p.y)}`);
    const el2 = $('#pixelInfo');
    el2.innerHTML = '';
    const sw = el('span', 'swatch');
    sw.style.background = info.color;
    el2.appendChild(sw);
    el2.appendChild(document.createTextNode(` (${info.x}, ${info.y}) ${info.color}`));
    el2.classList.remove('hidden');
  } catch (e) { /* нет снимка — молчим */ }
}

function setMode(m) {
  mode = m;
  $('#modeSample').classList.toggle('active', m === 'sample');
  $('#modeLabel').classList.toggle('active', m === 'label');
  $('#modeRegion').classList.toggle('active', m === 'region');
  $('#sampleCtl').classList.toggle('hidden', m !== 'sample');
  $('#labelCtl').classList.toggle('hidden', m !== 'label');
  $('#regionCtl').classList.toggle('hidden', m !== 'region');
  draw();
}
$('#modeSample').onclick = () => setMode('sample');
$('#modeLabel').onclick = () => setMode('label');
$('#modeRegion').onclick = () => setMode('region');

function liveOn() { return $('#liveChk').checked; }

$('#liveChk').onchange = () => {
  clearInterval(liveTimer);
  if (liveOn()) {
    liveTimer = setInterval(() => {
      if (document.hidden || !$('#tab-screen').classList.contains('active')) return;
      loadFrame('/api/screen.jpg?ts=' + Date.now());
    }, 1200);
    loadFrame('/api/screen.jpg?ts=' + Date.now());
  }
};

async function takeShot(delay) {
  $('#liveChk').checked = false;
  clearInterval(liveTimer);
  if (delay) toast('📸 Снимок через ' + delay + ' сек — переключись на нужное окно');
  try {
    await api('/api/frame/capture', { method: 'POST', body: JSON.stringify({ delay }) });
    editingId = null;
    $('#editBadge').classList.add('hidden');
    boxes = [];
    sel = null;
    loadFrame('/api/frame.jpg?ts=' + Date.now());
    if (!delay) toast('📸 Снимок готов — выдели область');
  } catch (e) { toast(e.message, true); }
}
$('#btnShot').onclick = () => takeShot(0);
$('#btnShot5').onclick = () => takeShot(5);

$('#btnSaveTpl').onclick = async () => {
  if (editingId) return toast('Сейчас открыт снимок из обучения — для образца сделай новый снимок', true);
  if (!sel) return toast('Сначала выдели область на снимке', true);
  const name = $('#tplName').value.trim();
  if (!name) return toast('Дай образцу имя', true);
  try {
    await api('/api/templates', { method: 'POST', body: JSON.stringify({ name, ...sel }) });
    sel = null;
    draw();
    toast('💾 Образец сохранён');
    await refreshTemplates();
    renderBlocks();  // чтобы образец появился в выпадающих списках блоков
  } catch (e) { toast(e.message, true); }
};

$('#btnUndoBox').onclick = () => { boxes.pop(); draw(); };

$('#btnSaveRegion').onclick = async () => {
  if (editingId) return toast('Сейчас открыт снимок из обучения — сделай новый снимок', true);
  if (!sel) return toast('Сначала выдели область на снимке', true);
  const name = $('#regionName').value.trim();
  if (!name) return toast('Дай области имя', true);
  try {
    await api('/api/regions', { method: 'POST', body: JSON.stringify({ name, ...sel }) });
    sel = null;
    toast('📐 Область сохранена');
    await refreshRegions();
    renderBlocks();
    draw();
  } catch (e) { toast(e.message, true); }
};

$('#btnOcrTest').onclick = async () => {
  if (!sel) return toast('Выдели область с текстом на снимке', true);
  $('#btnOcrTest').disabled = true;
  try {
    const res = await api('/api/ocr_test', { method: 'POST',
      body: JSON.stringify({ ...sel, digits: $('#ocrDigits').checked }) });
    toast(res.text ? ('🔤 Прочитано: «' + res.text + '»') : 'Ничего не прочиталось — выдели точнее или увеличь область', !res.text);
  } catch (e) { toast(e.message, true); }
  finally { $('#btnOcrTest').disabled = false; }
};

async function refreshRegions() {
  regions = await api('/api/regions');
  listsLoaded.region = true;
  const list = $('#regionList');
  list.innerHTML = '';
  const names = Object.keys(regions);
  if (!names.length) list.appendChild(el('p', 'hint', 'Пока нет областей. Выдели место на снимке в режиме «Область».'));
  names.forEach(name => {
    const r = regions[name];
    const card = el('div', 'card');
    card.appendChild(el('div', 'name', '📐 ' + name));
    card.appendChild(el('div', 'muted', `${r.w}×${r.h} в (${r.x}, ${r.y})`));
    const row = el('div', 'row');
    const del = el('button', 'btn', '🗑');
    del.onclick = async () => {
      if (!confirm('Удалить область «' + name + '»?')) return;
      await api('/api/regions/' + encodeURIComponent(name), { method: 'DELETE' });
      await refreshRegions();
      renderBlocks();
      draw();
    };
    row.appendChild(del);
    card.appendChild(row);
    list.appendChild(card);
  });
}

$('#btnSaveShot').onclick = async () => {
  if (!boxes.length) return toast('Обведи хотя бы один объект рамкой', true);
  try {
    if (editingId) {
      await api('/api/dataset/' + editingId + '/labels',
        { method: 'PUT', body: JSON.stringify({ boxes }) });
      toast('💾 Рамки обновлены');
    } else {
      await api('/api/dataset/save', { method: 'POST', body: JSON.stringify({ boxes }) });
      toast('📚 Снимок добавлен в обучение');
      boxes = [];
      draw();
    }
    refreshDataset();
  } catch (e) { toast(e.message, true); }
};

async function refreshTemplates() {
  templates = await api('/api/templates');
  listsLoaded.tpl = true;
  const list = $('#tplList');
  list.innerHTML = '';
  if (!templates.length) list.appendChild(el('p', 'hint', 'Пока нет образцов.'));
  templates.forEach(name => {
    const card = el('div', 'card');
    const img = document.createElement('img');
    img.src = '/api/templates/' + encodeURIComponent(name) + '.png?ts=' + Date.now();
    card.appendChild(img);
    card.appendChild(el('div', 'name', name));
    const row = el('div', 'row');
    const del = el('button', 'btn', '🗑');
    del.onclick = async () => {
      if (!confirm('Удалить образец «' + name + '»?')) return;
      await api('/api/templates/' + encodeURIComponent(name), { method: 'DELETE' });
      await refreshTemplates();
      renderBlocks();
    };
    row.appendChild(del);
    card.appendChild(row);
    list.appendChild(card);
  });
}

/* ---------------- обучение ---------------- */

async function refreshDataset() {
  const items = await api('/api/dataset');
  $('#dsCount').textContent = items.length;
  const list = $('#dsList');
  list.innerHTML = '';
  if (!items.length) list.appendChild(el('p', 'hint', 'Пока нет снимков. Добавь их на вкладке «Экран» в режиме «Разметка».'));
  items.forEach(it => {
    const card = el('div', 'card');
    const img = document.createElement('img');
    img.src = '/api/dataset/' + it.id + '/thumb.jpg';
    img.loading = 'lazy';
    card.appendChild(img);
    card.appendChild(el('div', 'muted', it.boxes + ' рамок · ' + (it.classes.join(', ') || '—')));
    const row = el('div', 'row');
    const edit = el('button', 'btn', '✏ Рамки');
    edit.onclick = () => editDatasetItem(it.id);
    const del = el('button', 'btn', '🗑');
    del.onclick = async () => {
      if (!confirm('Удалить этот снимок из обучения?')) return;
      await api('/api/dataset/' + it.id, { method: 'DELETE' });
      refreshDataset();
    };
    row.appendChild(edit);
    row.appendChild(del);
    card.appendChild(row);
    list.appendChild(card);
  });
}

async function editDatasetItem(id) {
  try {
    const data = await api('/api/dataset/' + id + '/labels');
    editingId = id;
    boxes = data.boxes || [];
    sel = null;
    $('#liveChk').checked = false;
    clearInterval(liveTimer);
    $('#editBadge').textContent = '✏ Правка снимка из обучения';
    $('#editBadge').classList.remove('hidden');
    switchTab('screen');
    setMode('label');
    loadFrame('/api/dataset/' + id + '/image.png');
  } catch (e) { toast(e.message, true); }
}

$('#btnTrain').onclick = async () => {
  const name = $('#modelName').value.trim();
  if (!name) return toast('Дай модели имя', true);
  try {
    await api('/api/train', { method: 'POST',
      body: JSON.stringify({ name, epochs: parseInt($('#epochs').value) || 15 }) });
    toast('🧠 Обучение началось — ход виден здесь и в журнале');
  } catch (e) { toast(e.message, true); }
};

async function refreshModels() {
  modelsList = await api('/api/models');
  listsLoaded.model = true;
  const list = $('#modelList');
  list.innerHTML = '';
  if (!modelsList.length) list.appendChild(el('p', 'hint', 'Пока нет обученных моделей.'));
  modelsList.forEach(m => {
    const row = el('div', 'model-row');
    row.appendChild(el('b', null, m.name));
    row.appendChild(el('span', 'muted', 'классы: ' + (m.classes || []).join(', ')));
    if (m.accuracy != null) row.appendChild(el('span', 'muted', 'точность ' + Math.round(m.accuracy * 100) + '%'));
    if (m.trained_at) row.appendChild(el('span', 'muted', m.trained_at));
    const del = el('button', 'btn', '🗑');
    del.onclick = async () => {
      if (!confirm('Удалить модель «' + m.name + '»?')) return;
      await api('/api/models/' + encodeURIComponent(m.name), { method: 'DELETE' });
      await refreshModels();
      renderBlocks();
    };
    row.appendChild(del);
    list.appendChild(row);
  });
  const sel2 = $('#testModel');
  sel2.innerHTML = '';
  if (!modelsList.length) sel2.appendChild(new Option('— нет моделей —', ''));
  modelsList.forEach(m => sel2.appendChild(new Option(m.name, m.name)));
}

$('#btnTest').onclick = async () => {
  const model = $('#testModel').value;
  if (!model) return toast('Сначала обучи модель', true);
  $('#btnTest').disabled = true;
  toast('🔎 Ищу объекты на экране…');
  try {
    const res = await api('/api/detect_test', { method: 'POST',
      body: JSON.stringify({ model, conf: parseFloat($('#testConf').value) || 0.6 }) });
    const img = new Image();
    img.onload = () => {
      const tcv = $('#testCv');
      tcv.width = img.naturalWidth;
      tcv.height = img.naturalHeight;
      tcv.classList.remove('hidden');
      const tctx = tcv.getContext('2d');
      tctx.drawImage(img, 0, 0);
      tctx.lineWidth = 2;
      tctx.font = '14px sans-serif';
      res.boxes.forEach(b => {
        const x = b.x * res.scale, y = b.y * res.scale,
              w = b.w * res.scale, h = b.h * res.scale;
        tctx.strokeStyle = '#ffc857';
        tctx.strokeRect(x, y, w, h);
        const label = b.cls + ' ' + Math.round(b.score * 100) + '%';
        const tw2 = tctx.measureText(label).width + 8;
        tctx.fillStyle = 'rgba(255,200,87,.9)';
        tctx.fillRect(x, Math.max(0, y - 18), tw2, 18);
        tctx.fillStyle = '#221a04';
        tctx.fillText(label, x + 4, Math.max(13, y - 5));
      });
      toast(res.boxes.length ? ('Найдено объектов: ' + res.boxes.length) : 'Ничего не найдено — попробуй понизить уверенность или доучить модель');
    };
    img.src = res.image;
  } catch (e) { toast(e.message, true); }
  finally { $('#btnTest').disabled = false; }
};

/* ---------------- журнал ---------------- */

function addLogLine(item) {
  const box = $('#logBox');
  const line = el('div', 'log-line ' + (item.level || 'info'));
  const t = el('span', 't', item.time);
  line.appendChild(t);
  line.appendChild(document.createTextNode(item.msg));
  box.appendChild(line);
  while (box.children.length > 800) box.removeChild(box.firstChild);
  if ($('#autoScroll').checked) box.scrollTop = box.scrollHeight;
}

$('#btnClearLog').onclick = () => { $('#logBox').innerHTML = ''; };

/* ---------------- снимок по горячей клавише ---------------- */

function gotoScreen(item) {
  editingId = null;
  $('#editBadge').classList.add('hidden');
  boxes = [];
  sel = null;
  $('#liveChk').checked = false;
  clearInterval(liveTimer);
  switchTab('screen');
  setMode(item.mode === 'label' ? 'label' : 'region');
  loadFrame('/api/frame.jpg?ts=' + Date.now());
  toast(item.mode === 'label'
    ? '📸 Снимок из игры — обведи объекты рамками и нажми «В обучение»'
    : '📸 Снимок из игры — выдели область или образец');
}

/* ---------------- HUD и сигналы ---------------- */

function updateHudBar(lines) {
  const bar = $('#hudBar');
  bar.innerHTML = '';
  if (!lines || !lines.length) return bar.classList.add('hidden');
  lines.forEach(t => bar.appendChild(el('span', 'hud-line', t)));
  bar.classList.remove('hidden');
}

let audioCtx = null;
document.addEventListener('pointerdown', () => {
  // браузер разрешает звук только после первого нажатия пользователя
  if (!audioCtx) {
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {}
  }
}, { once: true });

function playBeep() {
  if (!audioCtx) return;
  [0, 0.25].forEach(delay => {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.25, audioCtx.currentTime + delay);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + delay + 0.2);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(audioCtx.currentTime + delay);
    osc.stop(audioCtx.currentTime + delay + 0.22);
  });
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(proto + '://' + location.host + '/ws');
  ws.onmessage = e => {
    const item = JSON.parse(e.data);
    if (item.kind === 'hud') updateHudBar(item.lines);
    else if (item.kind === 'beep') { playBeep(); toast('🔔 ' + item.msg); }
    else if (item.kind === 'goto') gotoScreen(item);
    else addLogLine(item);
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
}

/* ---------------- статистика ---------------- */

async function refreshStats() {
  const data = await api('/api/stats?limit=300');
  const table = $('#statsTable');
  table.innerHTML = '';
  if (!data.rows.length) {
    table.innerHTML = '<tr><td class="hint">Пока пусто — добавь в сценарий блок «📊 Записать статистику»</td></tr>';
    return;
  }
  const trh = el('tr');
  data.header.forEach(h => trh.appendChild(el('th', null, h)));
  table.appendChild(trh);
  data.rows.forEach(r => {
    const tr = el('tr');
    r.forEach(c => tr.appendChild(el('td', null, c)));
    table.appendChild(tr);
  });
}

$('#btnStatsRefresh').onclick = () => refreshStats().catch(e => toast(e.message, true));
$('#btnStatsClear').onclick = async () => {
  if (!confirm('Удалить всю статистику?')) return;
  await api('/api/stats', { method: 'DELETE' });
  refreshStats();
};

/* ---------------- старт ---------------- */

renderBlocks();
refreshScenarioList().catch(() => {});
refreshTemplates().then(renderBlocks).catch(() => {});
refreshDataset().catch(() => {});
refreshModels().then(renderBlocks).catch(() => {});
refreshRegions().then(renderBlocks).catch(() => {});
connectWS();
refreshStatus();
setInterval(refreshStatus, 1500);
