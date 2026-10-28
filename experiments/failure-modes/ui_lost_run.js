// A run the server lost (404 on /events, e.g. after a restart) must end with "Connection lost", not a spinning timeline.
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const pg = await (await b.newContext()).newPage();
  await pg.route('**/runs/*/events', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"unknown or expired run"}' }));
  await pg.goto('http://localhost:8080/index.html?api=http://localhost:8030');
  await pg.setInputFiles('#file', '/home/user/Hephaestus/experiments/reading-spike/sketches/01_plate.png');
  await pg.waitForFunction(() => /disconnected/.test(document.querySelector('#tb-status').textContent), null, { timeout: 20000 })
    .then(() => console.log('handled'), () => console.log('NOT handled'));
  console.log(await pg.textContent('#tb-status'), '|', (await pg.$$eval('.timeline .step', (s) => s.at(-1).innerText)).replace(/\s+/g, ' '));
  await b.close();
})();
