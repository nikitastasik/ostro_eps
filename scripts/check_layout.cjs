// Проверка вёрстки: на всех страницах scrollWidth === innerWidth при 390/768/1024/1440, без ошибок в консоли.
// Запуск на работающем приложении: BASE=http://localhost:8080 node scripts/check_layout.cjs (нужен Playwright:
// npm i -g playwright или PW_PATH=путь к модулю). SHOTS=папка — сохранить скриншоты страниц при ширине 390.
const { chromium } = require(process.env.PW_PATH || 'playwright');
const BASE = process.env.BASE || 'http://localhost:8080';
const SHOTS = process.env.SHOTS || '';
const Q = 'тихий номер с видом на закат недалеко от моря';
(async () => {
  const browser = await chromium.launch();
  const api = await (await browser.newContext()).newPage();
  const res = await (await api.request.post(BASE + '/api/search', { data: { query: Q, limit: 3 } })).json();
  const ids = res.results.map((r) => r.hotel.id);
  const pages = [
    ['поиск', '/'], ['выдача', '/?q=' + encodeURIComponent(Q)], ['отели', '/hotels'],
    ['карточка', `/hotel/${encodeURIComponent(ids[0])}?q=${encodeURIComponent(Q)}`],
    ['сравнение', `/compare?ids=${ids.map(encodeURIComponent).join(',')}&q=${encodeURIComponent(Q)}`],
    ['кабинет', `/hotelier?hotel=${encodeURIComponent(ids[0])}`], ['агенты', '/agents'], ['характеристики', '/characteristics'],
    ['метрики', '/metrics'],
  ];
  if (process.env.TOUR) pages.push(['тур', '/tour']);
  let bad = 0;
  for (const w of [390, 768, 1024, 1440]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: w === 390 ? 844 : 900 }, hasTouch: w === 390 });
    const page = await ctx.newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push(e.message));
    page.on('console', (m) => { if (m.type() === 'error' && !/tile\.openstreetmap|ERR_|Failed to load resource/.test(m.text())) errs.push(m.text()); });
    await page.goto(BASE + '/hotelier', { waitUntil: 'networkidle' });
    if (await page.locator('button:has-text("Войти")').count()) await page.click('button:has-text("Войти")');
    await page.waitForTimeout(300);
    const row = [];
    for (const [name, path] of pages) {
      await page.goto(BASE + path, { waitUntil: 'networkidle' });
      await page.waitForTimeout(700);
      const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
      const ok = m.sw <= m.iw && m.iw === w;
      if (!ok) bad++;
      row.push(`${name}:${ok ? 'ok' : m.sw}`);
      if (SHOTS && w === 390) await page.screenshot({ path: `${SHOTS}/m390_${name}.png`, fullPage: false });
    }
    console.log(w, row.join('  '), errs.length ? '| ERR: ' + errs.slice(0, 3).join(' ; ') : '');
    await ctx.close();
  }
  await browser.close();
  console.log(bad ? `FAIL: ${bad} pages with horizontal scroll` : "ALL OK");
  process.exitCode = bad ? 1 : 0;
})();
