// UI screenshot of the number chips: MOCK_MODELS=1 MOCK_DATA_DIR=experiments/numbers/mock_data on :8040, web on :8080.
// node ui_chips.js before.png after.png
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const pg = await (await b.newContext({ viewport: { width: 1400, height: 1050 } })).newPage();
  pg.on('pageerror', (e) => console.log('pageerror', e.message));
  await pg.goto('http://localhost:8080/index.html?api=http://localhost:8040');
  await pg.setInputFiles('#file', '/home/user/Hephaestus/experiments/hand-sketches/sketches/03_block.png');
  await pg.waitForSelector('.chip-num.uncertain', { timeout: 60000 });
  await pg.waitForFunction(() => /verified|built|disputed|stuck|needs/.test(document.querySelector('#tb-status').textContent), null, { timeout: 60000 }).catch(() => {});
  console.log('chips:', await pg.$$eval('.chip-num', (cs) => cs.map((c) => `${c.textContent}:${c.className.replace('chip-num ', '')}`).join(' ')));
  console.log('step:', await pg.$$eval('.timeline .step b', (bs) => bs.map((x) => x.textContent).filter((t) => /numbers/.test(t)).join('|')));
  const unc = await pg.$('.chip-num.uncertain');
  await unc.click();
  await pg.waitForSelector('.chip-pop');
  const fig = await pg.$('.views'); await fig.screenshot({ path: process.argv[2] });
  const second = (await pg.$$('.chip-pop .btn'))[1];
  console.log('choices:', await pg.$$eval('.chip-pop .btn', (bs) => bs.map((x) => x.textContent).join(' ')));
  await second.click();
  await pg.waitForFunction(() => /confirmed|failed/.test(document.querySelector('#tb-status').textContent), null, { timeout: 30000 }).catch(() => {});
  console.log('after tap:', await pg.textContent('#tb-status'), '|', await pg.textContent('#edit-status'), '| size', await pg.textContent('#tb-size'));
  await (await pg.$('.views')).screenshot({ path: process.argv[3] });
  await b.close();
})();
