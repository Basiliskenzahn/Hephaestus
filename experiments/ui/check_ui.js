// End-to-end UI check of the web app against mock backends (no model calls), on desktop and phone widths.
//   web:    cd web && python -m http.server 8080
//   mocks (all with RUNS_PER_IP_PER_HOUR=1000):  MOCK_MODELS=1 MOCK_DELAY=1.5 uvicorn server.api:create_app --factory --port 8040                (gallery recordings)
//           MOCK_MODELS=1 MOCK_DELAY=0.5 MOCK_DATA_DIR=experiments/numbers/mock_data ... --port 8041      (block, 60 vs 40)
//           MOCK_MODELS=1 MOCK_DATA_DIR=experiments/failure-modes/mock_data ... --port 8030             (failure modes)
//   node check_ui.js [screenshot dir]
// Prints one line per check (ok / FAIL) and writes the first-screen and in-progress screenshots.
const { chromium } = require('playwright');
const WEB = 'http://localhost:8080/index.html';
const ROOT = '/home/user/Hephaestus';
const OUT = process.argv[2] || `${ROOT}/docs/screenshots`;
const results = [];
const allErrors = [];
const check = (name, ok, detail = '') => { results.push({ name, ok }); console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ' · ' + detail : ''}`); };
const FINAL = /verified|built, 1 note|needs input|error|budget|stuck|disputed|max iters|oscillating|disconnected|offline|busy/;

async function page(b, w, h) {
  const ctx = await b.newContext({ viewport: { width: w, height: h }, isMobile: w < 600, hasTouch: w < 600, deviceScaleFactor: w < 600 ? 2 : 1,
    permissions: ['microphone'] });
  const pg = await ctx.newPage();
  pg.errors = [];
  pg.on('console', (m) => { if (m.type() === 'error') allErrors.push(m.text()); });
  pg.on('pageerror', (e) => allErrors.push(e.message));
  await pg.addInitScript(() => {
    window.__cls = 0;
    new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; })
      .observe({ type: 'layout-shift', buffered: true });
  });
  return pg;
}
const finalStatus = (pg, t = 60000) => pg.waitForFunction((re) => new RegExp(re).test(document.querySelector('#tb-status').textContent), FINAL.source, { timeout: t });

async function run(b, label, w, h) {
  const tag = (s) => `${label}: ${s}`;
  let pg = await page(b, w, h);

  // 1. the default experience: the catch-and-fix replay starts on its own
  await pg.goto(`${WEB}?api=http://localhost:8040`);
  await pg.waitForSelector('#banner:not([hidden])', { timeout: 30000 });
  await pg.waitForTimeout(1200);
  const first = await pg.evaluate(() => ({
    banner: document.querySelector('#banner').innerText.replace(/\s+/g, ' '), active: document.querySelectorAll('.timeline .step.active').length,
    moments: [...document.querySelectorAll('.step.moment b')].map((x) => x.innerText.replace(/\s+/g, ' ')),
    width: document.documentElement.scrollWidth, cls: window.__cls, title: document.title,
    og: document.querySelector('meta[property="og:image"]')?.content, scrollY: window.scrollY,
  }));
  check(tag('autoplay replay ends on CAUGHT'), /^CAUGHT/.test(first.banner), first.banner.slice(0, 90));
  check(tag('no step left running'), first.active === 0);
  check(tag('caught and fixed moments marked'), first.moments.some((m) => /CAUGHT/.test(m)) && first.moments.some((m) => /FIXED/.test(m)), first.moments.join(' | '));
  check(tag('no horizontal scroll'), first.width <= w, `scrollWidth ${first.width}`);
  check(tag('page did not scroll by itself'), first.scrollY === 0);
  check(tag('layout shift'), first.cls < 0.1, `CLS ${first.cls.toFixed(3)}`);
  await pg.evaluate(() => window.scrollTo(0, 0));
  await pg.screenshot({ path: `${OUT}/first_screen_${label}.png` });

  // 3D viewer tools
  await pg.click('#dims-toggle');
  const dims = await pg.$$eval('.dim-label', (l) => l.map((x) => x.textContent));
  check(tag('dimensions overlay'), dims.length === 3, dims.join(', '));
  await pg.click('#view-reset');
  check(tag('details toggle shows raw model info'), await pg.evaluate(() => {
    document.querySelector('#raw-toggle').click();
    const shown = [...document.querySelectorAll('.raw')].some((r) => getComputedStyle(r).display !== 'none');
    document.querySelector('#raw-toggle').click();
    return shown && [...document.querySelectorAll('.raw')].every((r) => getComputedStyle(r).display === 'none');
  }));

  // slider + undo (rebuilds through /compile, no model call)
  const before = await pg.textContent('#tb-size');
  const slider = await pg.$('.pgroup:has(h3:text-is("Overall")) .param input[type=range]') || await pg.$('.param input[type=range]');
  await slider.focus();
  for (let i = 0; i < 8; i++) await pg.keyboard.press('ArrowRight');
  await slider.evaluate((x) => x.dispatchEvent(new Event('change')));
  await pg.waitForFunction(() => /rebuilt in/.test(document.querySelector('#tb-status').textContent), null, { timeout: 15000 }).catch(() => {});
  const after = await pg.textContent('#tb-size');
  check(tag('slider rebuilds the part'), /rebuilt in/.test(await pg.textContent('#tb-status')), `${before} → ${after}`);
  check(tag('undo enabled after a slider change'), !(await pg.$eval('#edit-undo', (x) => x.disabled)));
  await pg.click('#edit-undo');
  await pg.waitForTimeout(1500);
  check(tag('undo restores the size'), (await pg.textContent('#tb-size')) === before, await pg.textContent('#tb-size'));

  // downloads of the replayed part
  const links = await pg.$$eval('.downloads a', (as) => as.map((a) => ({ t: a.textContent, href: a.href, dis: a.getAttribute('aria-disabled') })));
  for (const l of links) {
    const ok = l.dis === 'false' && (l.href.startsWith('blob:') || (await pg.evaluate(async (u) => (await fetch(u)).ok, l.href)));
    check(tag(`download ${l.t}`), ok);
  }

  // 2. a live (mock) run in progress, then typed and spoken edits on the L-bracket recording
  await pg.goto(`${WEB}?api=http://localhost:8040&autoplay=0`);
  await pg.setInputFiles('#file', `${ROOT}/experiments/reading-spike/sketches/02_l_bracket.png`);
  await pg.waitForSelector('#viewer canvas', { timeout: 30000 });
  await pg.waitForTimeout(400);
  const live = await pg.evaluate(() => ({ clock: document.querySelector('#clock').textContent, active: document.querySelectorAll('.step.active').length }));
  check(tag('live run shows steps as they start + a clock'), live.active === 1 && /^\d:\d\d$/.test(live.clock), `clock ${live.clock}`);
  if (label === 'desktop') await pg.screenshot({ path: `${OUT}/run_in_progress_desktop.png` });
  else { await pg.evaluate(() => document.querySelector('.views').scrollIntoView()); await pg.screenshot({ path: `${OUT}/run_in_progress_phone.png` }); }
  await finalStatus(pg).catch(() => {});
  check(tag('live mock run finishes'), /verified|built/.test(await pg.textContent('#tb-status')), await pg.textContent('#tb-status'));
  const part = await pg.textContent('#tb-part');
  const instr = /l bracket/.test(part) ? 'make the holes M6' : /plate/.test(part) ? 'width 100' : 'make the holes M6';
  await pg.fill('#edit-text', instr);
  await pg.click('#edit-apply');
  await pg.waitForFunction(() => /edited|question|edit failed/.test(document.querySelector('#tb-status').textContent), null, { timeout: 30000 }).catch(() => {});
  check(tag(`typed edit "${instr}" on ${part}`), /edited|question/.test(await pg.textContent('#tb-status')), (await pg.textContent('#edit-status')).slice(0, 100));
  const hasMic = await pg.$eval('#mic', (m) => !m.hidden);
  if (hasMic) {
    await pg.click('#mic');
    await pg.waitForTimeout(1500);
    await pg.click('#mic');
    await pg.waitForFunction(() => /Heard|Could not|nothing/.test(document.querySelector('#edit-status').textContent), null, { timeout: 20000 }).catch(() => {});
    check(tag('voice edit transcribes into the box'), (await pg.inputValue('#edit-text')).length > 0, (await pg.textContent('#edit-status')).slice(0, 80));
  }
  await pg.close();

  // 3. number chips: the block read as 60, re-read as 40; one tap sets it (no model call)
  pg = await page(b, w, h);
  await pg.goto(`${WEB}?api=http://localhost:8041&autoplay=0`);
  await pg.setInputFiles('#file', `${ROOT}/experiments/hand-sketches/sketches/03_block.png`);
  await pg.waitForSelector('.chip-num.uncertain', { timeout: 60000 });
  await finalStatus(pg).catch(() => {});
  check(tag('banner asks to confirm numbers'), /^CHECK/.test(await pg.innerText('#banner')), (await pg.innerText('#banner')).replace(/\s+/g, ' '));
  const chip = (await pg.$$('.chip-num.uncertain')).find(async (c) => (await c.textContent()).startsWith('60')) || (await pg.$('.chip-num.uncertain'));
  const chips = await pg.$$('.chip-num.uncertain');
  let sixty = null;
  for (const c of chips) if ((await c.textContent()).startsWith('60')) sixty = c;
  await (sixty || chip).click();
  await pg.waitForSelector('.chip-pop');
  const choices = await pg.$$eval('.chip-pop .btn', (bs) => bs.map((x) => x.textContent));
  await (await pg.$$('.chip-pop .btn'))[1].click();
  await pg.waitForFunction(() => /confirmed|failed/.test(document.querySelector('#tb-status').textContent), null, { timeout: 20000 }).catch(() => {});
  check(tag('chip tap 60 → 40 rebuilds'), /70 × 40 × 20/.test(await pg.textContent('#tb-size')), `choices ${choices.join('/')} → ${await pg.textContent('#tb-size')}`);
  await pg.close();

  // 4. failure messages
  pg = await page(b, w, h);
  // the failure mock serves its 4 recordings in turn (not a drawing, read timeout, never compiles, correction fails):
  // 4 uploads must end in these 4 outcomes, in whatever order the rotation is at
  const want = ['needs input', 'error', 'error', 'disputed'];
  const got = [];
  for (const name of ['1', '2', '3', '4']) {
    await pg.goto(`${WEB}?api=http://localhost:8030&autoplay=0`);
    await pg.setInputFiles('#file', `${ROOT}/experiments/reading-spike/sketches/01_plate.png`);
    await finalStatus(pg).catch(() => {});
    await pg.waitForTimeout(500);
    const r = await pg.evaluate(() => ({ status: document.querySelector('#tb-status').textContent,
      active: document.querySelectorAll('.timeline .step.active').length,
      last: [...document.querySelectorAll('.timeline .step')].at(-1)?.innerText.replace(/\s+/g, ' ').slice(0, 110) }));
    got.push(r.status.replace(/stuck|max iters/, 'disputed'));
    check(tag(`failure run ${name} ends cleanly`), r.active === 0 && FINAL.test(r.status), `${r.status} · ${r.last}`);
  }
  check(tag('failure outcomes: not a drawing, timeout, never compiles, correction fails'),
    JSON.stringify([...got].sort()) === JSON.stringify([...want].sort()), got.join(', '));
  const routes = [
    ['upload refused (tiny image)', null, true, false, /error/],
    ['server unreachable', (route) => route.abort(), null, false, /offline/],
    ['busy (429)', (route) => route.fulfill({ status: 429, contentType: 'application/json', body: '{"message":"Talos is busy with other runs. Try again in a minute."}' }), null, false, /busy/],
    ['lost run (404 on events)', null, null, true, /disconnected/],
  ];
  for (const [name, handler, file, lost, want] of routes) {
    const p2 = await page(b, w, h);
    if (handler) await p2.route('**/runs', handler);
    if (lost) await p2.route('**/runs/*/events', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"unknown or expired run"}' }));
    await p2.goto(`${WEB}?api=http://localhost:8030&autoplay=0`);
    if (file) {
      // a 64 x 64 PNG: below the server's minimum image side
      await p2.evaluate(async () => {
        const c = document.createElement('canvas'); c.width = c.height = 64;
        const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
        const dt = new DataTransfer(); dt.items.add(new File([blob], 'tiny.png', { type: 'image/png' }));
        const inp = document.querySelector('#file'); inp.files = dt.files; inp.dispatchEvent(new Event('change'));
      });
    } else await p2.setInputFiles('#file', `${ROOT}/experiments/reading-spike/sketches/01_plate.png`);
    await p2.waitForFunction(() => /error|offline|busy|disconnected/.test(document.querySelector('#tb-status').textContent), null, { timeout: 20000 }).catch(() => {});
    const r = await p2.evaluate(() => ({ status: document.querySelector('#tb-status').textContent, clock: document.querySelector('#clock').className,
      last: [...document.querySelectorAll('.timeline .step')].at(-1)?.innerText.replace(/\s+/g, ' ').slice(0, 110) }));
    check(tag(`failure ${name}`), want.test(r.status) && /stopped/.test(r.clock), `${r.status} · ${r.last}`);
    await p2.close();
  }
  // expected: the refused upload (400), the 429, the aborted request and the 404 the routes above fake
  const errs = allErrors.splice(0).filter((e) => !/Failed to load resource|ERR_FAILED|net::|Failed to fetch/.test(e));
  check(tag('no console errors'), errs.length === 0, errs.slice(0, 3).join(' | '));
  await pg.close();
}

(async () => {
  const b = await chromium.launch({ args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
  await run(b, 'desktop', 1440, 900);
  await run(b, 'phone', 390, 844);
  await b.close();
  const bad = results.filter((r) => !r.ok);
  console.log(`\n${results.length - bad.length}/${results.length} checks ok`);
})();
