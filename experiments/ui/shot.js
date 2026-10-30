// quick look: node shot.js <url> <out.png> <width> <height> [waitSelector]
const { chromium } = require('playwright');
(async () => {
  const [url, out, w, h, sel] = process.argv.slice(2);
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1, isMobile: +w < 600, hasTouch: +w < 600 });
  const pg = await ctx.newPage();
  pg.on('console', (m) => m.type() === 'error' && console.log('console error:', m.text()));
  pg.on('pageerror', (e) => console.log('pageerror', e.message));
  await pg.goto(url);
  if (sel) await pg.waitForSelector(sel, { timeout: 60000 });
  await pg.waitForTimeout(1500);
  await pg.screenshot({ path: out });
  await b.close();
})();
