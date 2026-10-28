// Production smoke test of a voice edit: replay the L-bracket example, speak "make the holes M6" through a fake
// microphone (Chromium flag, WAV from experiments/edits/audio), Apply. Two live calls: transcription + edit.
//   node voice_edit.js > voice_edit.result.json
const { chromium } = require('playwright');
const SITE = 'https://hephaestus-talos.netlify.app';
const API = 'https://david-julian-wermuth--hephaestus-web.us-east.modal.run';
(async () => {
  const b = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${__dirname}/../edits/audio/holes_m6.wav`] });
  const ctx = await b.newContext({ viewport: { width: 1400, height: 1000 } });
  await ctx.grantPermissions(['microphone'], { origin: SITE });
  const pg = await ctx.newPage();
  const out = {};
  await pg.goto(`${SITE}/?example=l_bracket&v=${Date.now()}`);
  await pg.waitForSelector('#edit-form:not([hidden])', { timeout: 90000 });
  await pg.click('#mic');
  await pg.waitForTimeout(3000);                       // the clip is ~2 s; the fake device loops it
  const t0 = Date.now();
  await pg.click('#mic');
  await pg.waitForFunction(() => /Heard|Could not|nothing/.test(document.querySelector('#edit-status').textContent), null, { timeout: 60000 });
  out.transcribe_ms = Date.now() - t0;                 // stop -> transcript shown (WAV encode + upload + ASR)
  out.transcribe_status = await pg.textContent('#edit-status');
  out.transcript = await pg.inputValue('#edit-text');
  const t1 = Date.now();
  await pg.click('#edit-apply');
  await pg.waitForFunction(() => /edited|failed|question/.test(document.querySelector('#tb-status').textContent), null, { timeout: 90000 });
  out.edit_ms = Date.now() - t1;                       // Apply -> part rebuilt and shown
  out.edit_status = await pg.textContent('#edit-status');
  out.badge = await pg.textContent('#tb-part');
  await pg.screenshot({ path: `${__dirname}/voice_edit.png` });
  const st = await (await pg.request.get(`${API}/status`)).json();
  out.server_timing = st.recent_runs.filter((r) => r.kind === 'transcribe' || r.kind === 'edit').slice(-2);
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
