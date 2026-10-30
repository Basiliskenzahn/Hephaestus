// One live run through the deployed site (real models): node live_run.js <image> <out prefix>
const { chromium } = require('playwright');
(async () => {
  const [img, out] = process.argv.slice(2);
  const b = await chromium.launch();
  const pg = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const errs = [];
  pg.on('console', (m) => m.type() === 'error' && errs.push(m.text()));
  let runId = null;
  pg.on('response', async (r) => { if (/\/runs$/.test(r.url()) && r.request().method() === 'POST') runId = (await r.json()).run_id; });
  await pg.goto('https://hephaestus-talos.netlify.app/?autoplay=0');
  const t0 = Date.now();
  await pg.setInputFiles('#file', img);
  await pg.waitForSelector('#viewer canvas', { timeout: 120000 });
  console.log('first part after', ((Date.now() - t0) / 1000).toFixed(1), 's');
  await pg.waitForFunction(() => /verified|built|needs input|error|budget|stuck|disputed|max iters|oscillating|disconnected/.test(document.querySelector('#tb-status').textContent), null, { timeout: 240000 });
  await pg.waitForTimeout(1500);
  console.log('done after', ((Date.now() - t0) / 1000).toFixed(1), 's; status', await pg.textContent('#tb-status'), '| run', runId);
  console.log('banner:', (await pg.innerText('#banner').catch(() => '')).replace(/\s+/g, ' '));
  console.log('chips:', await pg.$$eval('.chip-num', (cs) => cs.map((c) => `${c.textContent}:${c.className.replace('chip-num ', '')}`).join(' ')));
  console.log('steps:', await pg.$$eval('.timeline .step', (s) => s.map((x) => x.querySelector('b').innerText + ' [' + x.querySelector('.dur').textContent + ']').join(' | ')));
  await pg.evaluate(() => window.scrollTo(0, 0));
  await pg.screenshot({ path: `${out}_screen.png` });
  await (await pg.$('.views')).screenshot({ path: `${out}_views.png` });
  console.log('console errors:', errs.length ? errs.join(' | ') : 'none');
  await b.close();
})();
