// Скринкаст по сценарию docs/DEMO.md: Playwright проходит демо-тур с подписями на экране и пишет видео (webm).
// Запуск на свежем приложении: BASE=http://localhost:8080 OUT=video node scripts/demo_video.cjs
// В mp4: ffmpeg -i video/<файл>.webm -c:v libx264 -crf 26 -preset slow -pix_fmt yuv420p -movflags +faststart demo.mp4
const { chromium } = require(process.env.PW_PATH || 'playwright');
const BASE = process.env.BASE || 'http://localhost:8080';
const OUT = process.env.OUT || './video';

async function caption(page, text) {
  await page.evaluate((t) => {
    let el = document.getElementById('__cap');
    if (!el) {
      el = document.createElement('div');
      el.id = '__cap';
      el.style.cssText = 'position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:99999;background:rgba(19,33,59,.93);color:#fff;padding:12px 22px;border-radius:12px;font:600 19px Inter,-apple-system,Segoe UI,Roboto,sans-serif;max-width:82%;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.25);pointer-events:none;line-height:1.35';
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}
const wait = (page, ms) => page.waitForTimeout(ms);
async function slowScroll(page, dy, steps = 8, pause = 160) {
  for (let i = 0; i < steps; i++) { await page.mouse.wheel(0, dy / steps); await page.waitForTimeout(pause); }
}
async function scrollTo(page, selector, offset = 80) {
  await page.evaluate(([sel, off]) => {
    const el = document.querySelector(sel);
    if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - off, behavior: 'smooth' });
  }, [selector, offset]);
  await page.waitForTimeout(900);
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: OUT, size: { width: 1440, height: 900 } } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('response', (r) => { if (r.url().includes('/api/') && r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`); });
  const step = async (name, fn) => { const t0 = Date.now(); try { await fn(); console.log('ok:', name, Math.round((Date.now() - t0) / 1000) + 's'); } catch (e) { errors.push(name + ': ' + e.message); console.log('FAIL:', name, e.message); } };
  const tour = await (await page.request.get(BASE + '/api/tour')).json();
  const q = tour.query;
  const qq = encodeURIComponent(q);
  const hotel = (id, tail = '') => `${BASE}/hotel/${encodeURIComponent(id)}${tail}`;

  await step('тур', async () => {
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    await caption(page, 'Агенты собирают об отелях то, чего нет в карточках: вид из окна по этажам, закат, тишину, дорогу до моря');
    await wait(page, 5500);
    await page.click('.hero-tour');
    await page.waitForSelector('.tour-step');
    await caption(page, 'Демо-тур «Проверить за 5 минут»: девять шагов по требованиям задания, каждый открывает нужное состояние');
    await wait(page, 2500);
    await slowScroll(page, 1300, 12, 260);
    await wait(page, 1800);
  });

  await step('поиск', async () => {
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    const box = page.locator('.searchbox input');
    await box.click();
    await page.keyboard.type(q, { delay: 45 });
    await page.keyboard.press('Enter');
    await page.waitForSelector('.result', { timeout: 30000 });
    await scrollTo(page, '.understood', 90);
    await caption(page, 'Агент разобрал запрос на пожелания — правилами, без LLM — и оценил каждый отель по номерам «сторона × этаж»');
    await wait(page, 6500);
    await caption(page, 'Совет агента: какой номер просить; доля подходящих номеров; «✓ с условием» — если подходит не каждый номер');
    await wait(page, 7000);
    await caption(page, '«↑ Выше следующего» — почему отель стоит выше соседа по выдаче');
    await wait(page, 5000);
    await page.locator('.result button:has-text("Пожелание к бронированию")').first().click();
    await page.waitForSelector('.wish-text', { timeout: 20000 });
    await caption(page, 'Пожелание к бронированию: готовый текст для отеля — сторона, этаж, просьбы и вопросы о неизвестном');
    await wait(page, 7500);
    await page.keyboard.press('Escape');
    await wait(page, 500);
  });

  await step('панорама и лучи', async () => {
    const fh = tour.floor_hotel;
    await page.goto(hotel(fh.id, `?q=${qq}#view`), { waitUntil: 'networkidle' });
    await page.waitForSelector('.rays-map', { timeout: 30000 });
    await wait(page, 1200);
    await caption(page, `«${fh.name}»: панорама уже на рекомендованной стороне и этаже — по лучам через 1° по рельефу и высотам зданий`);
    await wait(page, 6500);
    await scrollTo(page, '.rays', 120);
    await caption(page, 'Те же 120 лучей на карте: синие уходят в море, красным — дом, который закрывает вид, пунктир — закат месяца');
    await wait(page, 6500);
    const slider = page.locator('#view input[type=range]').first();
    await slider.focus();
    await scrollTo(page, '.rays', 150);  // ползунок в фокусе, а на экране — карта лучей
    await caption(page, 'Этаж ниже — дом закрывает больше обзора; подпись пересчитывает градусы');
    for (let i = 0; i < 4; i++) { await page.keyboard.press('ArrowLeft'); await wait(page, 1300); }
    await wait(page, 1500);
    for (let i = 0; i < 4; i++) { await page.keyboard.press('ArrowRight'); await wait(page, 700); }
    await wait(page, 1000);
  });

  await step('достоверность', async () => {
    const eh = tour.evidence_hotel;
    await page.goto(hotel(eh.id, '#char-sea_view'), { waitUntil: 'networkidle' });
    await page.waitForSelector('#char-sea_view .fact', { timeout: 30000 });
    await wait(page, 1200);
    await caption(page, 'У каждого значения — факты источников: расчёт по геоданным и ответ отельера, уверенность, дата, срок годности');
    await wait(page, 6000);
    await page.locator('#char-sea_view .fact').last().evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' }));
    await wait(page, 900);
    await caption(page, 'Доказательства: из чего сложилась уверенность — этажность из OSM, соседи с известной высотой, застройка вокруг');
    await wait(page, 6500);
  });

  await step('агенты вживую', async () => {
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await wait(page, 700);
    await caption(page, 'Пересчёт вживую: план по рецептам → инструменты → решения арбитра → вопросы отельеру');
    await page.click('text=Пересчитать агентами');
    await page.waitForSelector('.drawer .log .st', { timeout: 30000 });
    await page.waitForSelector('.drawer .badge.ok', { timeout: 120000 });
    await wait(page, 2500);
    await page.locator('.drawer').evaluate((el) => el.scrollTo({ top: 600, behavior: 'smooth' }));
    await wait(page, 3500);
    await page.locator('.drawer').evaluate((el) => el.scrollTo({ top: 1400, behavior: 'smooth' }));
    await wait(page, 3500);
    await page.click('.drawer >> text=Закрыть');
  });

  await step('кабинет отельера', async () => {
    await page.goto(BASE + '/hotelier', { waitUntil: 'networkidle' });
    if (await page.locator('button:has-text("Войти")').count()) await page.click('button:has-text("Войти")');
    await page.goto(`${BASE}/hotelier?hotel=${encodeURIComponent(tour.conflict_hotel.id)}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('text=Что поднимет вас в выдаче', { timeout: 20000 });
    await caption(page, 'Кабинет отельера: вопросы по пользе — насколько ответ «да» поднимет отель по запросам гостей');
    await wait(page, 7000);
    await page.locator('h3:has-text("источники расходятся")').first().evaluate((el) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 90, behavior: 'smooth' }));
    await wait(page, 900);
    await caption(page, 'Источники расходятся: расчёт по геоданным против слов отельера — агент задаёт уточняющий вопрос');
    await wait(page, 6500);
    // ответ, согласный с расчётом: этаж и сторона из факта геоданных
    const conf = await (await page.request.get(BASE + '/api/conflicts?status=open&limit=500')).json();
    const c = conf.items.find((x) => x.hotel_id === tour.conflict_hotel.id);
    const geo = c && c.facts.find((f) => f.source === 'geo');
    const DIR = { 'С': 'север', 'СВ': 'северо-восток', 'В': 'восток', 'ЮВ': 'юго-восток', 'Ю': 'юг', 'ЮЗ': 'юго-запад', 'З': 'запад', 'СЗ': 'северо-запад' };
    const v = (geo && geo.value) || {};
    const answer = v.min_floor ? `Да, вы правы: море видно с ${v.min_floor} этажа, окна на ${DIR[v.best_facade] || 'море'}.` : 'Море из номеров не видно.';
    const card = page.locator('.qcard.open').first();
    await card.scrollIntoViewIfNeeded();
    await card.locator('textarea').click();
    await page.keyboard.type(answer, { delay: 40 });
    await card.locator('text=Как агент поймёт ответ?').click();
    await wait(page, 1200);
    await caption(page, 'Ответ свободным текстом агент раскладывает по полям характеристики');
    await wait(page, 4000);
    await card.locator('button:has-text("Отправить ответ")').click();
    await wait(page, 1500);
    await caption(page, 'Ответ сохранён как факт с источником «отельер»; арбитр пересобрал значение и снял противоречие');
    await wait(page, 4000);
  });

  await step('новая характеристика', async () => {
    await page.goto(`${BASE}/characteristics?add=${encodeURIComponent(tour.characteristic)}`, { waitUntil: 'networkidle' });
    await wait(page, 1200);
    await caption(page, 'Новая характеристика — описанием текстом, без программирования');
    await page.click('text=Скомпилировать');
    await page.waitForSelector('text=Рецепт агента', { timeout: 20000 });
    await caption(page, 'Компилятор превратил описание в рецепт: инструмент OSM, 10 минут пешком, фильтр «круглосуточно»');
    await wait(page, 6000);
    await page.click('text=Создать и запустить агентов по всем отелям');
    await page.waitForSelector('.drawer .badge.ok', { timeout: 300000 });
    await caption(page, `Агенты собрали новую характеристику для всех ${tour.hotels} отелей — она сразу работает в поиске`);
    await wait(page, 3500);
    await page.click('.drawer >> text=Закрыть');
  });

  await step('построили дом', async () => {
    const sh = tour.scenario_hotel;
    await page.goto(`${BASE}/agents?scenario=${encodeURIComponent(sh.id)}#scenario`, { waitUntil: 'networkidle' });
    await wait(page, 1500);
    await caption(page, `Актуализация: перед «морскими» окнами «${sh.name}» построили дом — агенты пересчитывают соседей`);
    await page.click('#scenario button:has-text("Построить")');
    await page.waitForSelector('.drawer .log .st', { timeout: 60000 });
    await wait(page, 7000);
    await page.click('.drawer >> text=Закрыть');
    await page.click('.built-link');
    await page.waitForSelector('.rays-map', { timeout: 30000 });
    await wait(page, 1500);
    await scrollTo(page, '.rays', 120);
    await caption(page, 'Новый дом на карте лучей: он закрывает часть «морского» обзора, подпись пересчитала градусы');
    await wait(page, 7500);
  });

  await step('сравнение', async () => {
    await page.goto(`${BASE}/compare?ids=${tour.top.map(encodeURIComponent).join(',')}&q=${qq}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('table.cmp', { timeout: 20000 });
    await caption(page, 'Сравнение под запрос: сначала пожелания гостя со статусами у каждого отеля и совет по номеру');
    await wait(page, 7500);
    await slowScroll(page, 600, 8, 200);
    await page.check('text=Только различия');
    await caption(page, '«Только различия» — остаются строки, где отели действительно отличаются');
    await wait(page, 5500);
  });

  await step('метрики', async () => {
    await page.goto(BASE + '/metrics', { waitUntil: 'networkidle' });
    await page.waitForSelector('text=Согласие ранжирования', { timeout: 60000 });
    await caption(page, 'Метрики: покрытие, точность на отложенных выборках, согласие ранжирования с разметкой, польза вопросов, сверка Солнца');
    await wait(page, 5000);
    await slowScroll(page, 900, 10, 300);
    await wait(page, 3500);
  });

  await step('сброс', async () => {
    await page.goto(BASE + '/tour', { waitUntil: 'networkidle' });
    await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }));
    await wait(page, 1200);
    page.once('dialog', (d) => d.accept());
    await caption(page, 'Кнопка «Вернуть демо» возвращает всё в исходное состояние — можно пройти тур заново');
    await page.click('button:has-text("Вернуть демо")');
    await page.waitForSelector('text=Демо возвращено в исходное состояние', { timeout: 120000 });
    await wait(page, 3000);
  });

  await ctx.close();
  await browser.close();
  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no errors');
})();
