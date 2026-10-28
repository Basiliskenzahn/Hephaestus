// Production smoke test of a typed edit on a replayed example (1 live call). node typed_edit.js voice_edit.png
const { chromium } = require('playwright');
const SITE = 'https://hephaestus-talos.netlify.app';
const API = 'https://david-julian-wermuth--hephaestus-web.us-east.modal.run';
(async () => {
  const b = await chromium.launch();
  const pg = await (await b.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  const out = {};
  await pg.goto(`${SITE}/?example=l_bracket&v=${Date.now()}`);
  await pg.waitForSelector('#edit-form:not([hidden])', { timeout: 90000 });
  await pg.fill('#edit-text', 'make the holes M6');
  const t1 = Date.now();
  await pg.click('#edit-apply');
  await pg.waitForFunction(() => /edited|failed|question/.test(document.querySelector('#tb-status').textContent), null, { timeout: 120000 });
  out.edit_ms = Date.now() - t1;
  out.edit_status = await pg.textContent('#edit-status');
  out.tb_status = await pg.textContent('#tb-status');
  out.badge = await pg.textContent('#tb-part');
  out.sliders = await pg.$$eval('.param label', (ls) => ls.map((l) => l.firstChild.textContent + '=' + (l.parentElement.querySelector('input[type=number]')?.value ?? '')));
  await pg.screenshot({ path: process.argv[2] });
  const st = await (await pg.request.get(`${API}/status`)).json();
  out.server_timing = st.recent_runs.slice(-2);
  out.spent_today_usd = st.spent_today_usd;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
