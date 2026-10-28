// UI check of the failure recordings in mock_data/ (MOCK_MODELS=1 MOCK_DATA_DIR=experiments/failure-modes/mock_data on :8030, web on :8080).
// node ui_failures.js /tmp/out  -> final status, banner, last timeline step, no step left active
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const pg = await (await b.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  pg.on('pageerror', (e) => console.log('pageerror', e.message));
  const out = [];
  for (const name of ['a_not_a_drawing', 'b_read_timeout', 'c_never_compiles', 'd_correction_fails']) {
    await pg.goto('http://localhost:8080/index.html?api=http://localhost:8030');
    await pg.setInputFiles('#file', '/home/user/Hephaestus/experiments/reading-spike/sketches/01_plate.png');
    const final = /verified|built, 1 note|needs input|error|budget|stuck|disputed|max_iters|oscillating/;
    await pg.waitForFunction((re) => new RegExp(re).test(document.querySelector('#tb-status').textContent), final.source, { timeout: 60000 }).catch(() => {});
    await pg.waitForTimeout(800);
    const r = await pg.evaluate(() => ({
      status: document.querySelector('#tb-status').textContent,
      banner: document.querySelector('#banner').hidden ? null : document.querySelector('#banner').textContent,
      active_steps: document.querySelectorAll('.timeline .step.active').length,
      last: [...document.querySelectorAll('.timeline .step')].slice(-1)[0]?.innerText.replace(/\s+/g, ' ').slice(0, 220),
      edit_box: !document.querySelector('#edit-form').hidden,
    }));
    out.push({ name, ...r });
    await pg.screenshot({ path: `${process.argv[2]}_${name}.png` });
  }
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
