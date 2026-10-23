// Скриншоты для README (docs/img): снимаются с работающего приложения, отели берутся из /api/tour.
// Запуск: BASE=http://localhost:8080 OUT=docs/img node scripts/readme_shots.cjs (нужен Playwright, см. check_layout.cjs).
// Каждый файл — JPEG до 300 КБ.
const { chromium } = require(process.env.PW_PATH || 'playwright');
const BASE = process.env.BASE || 'http://localhost:8080';
const OUT = process.env.OUT || 'docs/img';
const W = 1280;
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: 860 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const tour = await (await page.request.get(BASE + '/api/tour')).json();
  const q = encodeURIComponent(tour.query);
  const shot = async (name, opts = {}) => {
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${OUT}/${name}.jpg`, type: 'jpeg', quality: 84, ...opts });
    console.log('saved', name);
  };
  // 1. выдача: чипы пожеланий, «✓ с условием», совет по номеру, почему выше
  await page.goto(`${BASE}/?q=${q}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.result');
  await page.evaluate(() => {
    const bar = [...document.querySelectorAll('.card')].find((e) => e.textContent.includes('Как агент понял запрос'));
    window.scrollTo(0, bar.getBoundingClientRect().top + window.scrollY - document.querySelector('.topbar').offsetHeight - 12);
  });
  await shot('search');
  // 2. панорама и лучи на карте на рекомендованной стороне и этаже: два фрагмента карточки рядом
  const fh = tour.floor_hotel;
  await page.setViewportSize({ width: 1160, height: 1600 });
  await page.goto(`${BASE}/hotel/${encodeURIComponent(fh.id)}?q=${q}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rays-map');
  await page.waitForTimeout(2500);
  await page.evaluate(() => window.scrollTo(0, 0));
  const box = async (sel) => page.locator(sel).first().boundingBox();
  const card = await box('#view');
  const panoEnd = await box('#view p:has-text("-сторона,")');
  const raysTop = await box('#view h3:has-text("Куда смотрит окно")');
  const raysEnd = await box('#view .rays p');
  const pad = 16;
  const a = await page.screenshot({ fullPage: true, clip: { x: card.x, y: card.y, width: card.width, height: panoEnd.y + panoEnd.height + pad - card.y } });
  const b = await page.screenshot({ fullPage: true, clip: { x: card.x, y: raysTop.y - 12, width: card.width, height: raysEnd.y + raysEnd.height + pad + 12 - raysTop.y } });
  const img = (buf) => `<img src="data:image/png;base64,${buf.toString('base64')}" style="border-radius:14px;border:1px solid #e3e8ef;display:block">`;
  await page.setViewportSize({ width: Math.round(2 * card.width + 3 * pad), height: 400 });
  await page.setContent(`<body style="margin:0;padding:${pad}px;background:#f4f6fa;display:flex;gap:${pad}px;align-items:flex-start">${img(a)}${img(b)}</body>`);
  await page.waitForTimeout(300);
  await shot('view_rays', { fullPage: true });
  // 3. кабинет отельера: «Что поднимет вас в выдаче» и противоречие
  await page.setViewportSize({ width: W, height: 860 });
  await page.goto(`${BASE}/hotelier`, { waitUntil: 'networkidle' });
  if (await page.locator('button:has-text("Войти")').count()) await page.click('button:has-text("Войти")');
  await page.goto(`${BASE}/hotelier?hotel=${encodeURIComponent(tour.conflict_hotel.id)}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('text=Что поднимет вас в выдаче');
  await shot('hotelier');
  // 4. сравнение под запрос
  await page.goto(`${BASE}/compare?ids=${tour.top.map(encodeURIComponent).join(',')}&q=${q}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('table.cmp');
  await shot('compare');
  await browser.close();
})();
