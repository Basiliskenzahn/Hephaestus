import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const API = (window.HEPHAESTUS_API || "").replace(/\/$/, "");
const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) n.append(k);
  return n;
};
const fmt = (x, d = 1) => (Math.abs(x - Math.round(x)) < 1e-6 ? String(Math.round(x)) : x.toFixed(d));

// ------------------------------------------------------------------ state
let session = 0;              // bumps on every new run/replay; stale async work checks it
let current = null;           // {mode, base, title, model, events, spec, lastId, es}
let finalFiles = null;

// ------------------------------------------------------------------ 3D viewer
const viewer = (() => {
  let renderer, scene, camera, controls, group, host, fitted = false;
  const loader = new GLTFLoader();
  const ink = new THREE.LineBasicMaterial({ color: 0x1d2733 });
  const body = new THREE.MeshStandardMaterial({ color: 0xd9dee3, roughness: 0.85, metalness: 0.05,
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });

  function init() {
    host = $("#viewer");
    host.innerHTML = "";
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0xffffff);
    host.append(renderer.domElement, el("span", { class: "hint", text: "drag to rotate · scroll to zoom" }));
    scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f96, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(1, 2, 1.5);
    scene.add(sun);
    camera = new THREE.PerspectiveCamera(32, 1, 0.1, 5000);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    new ResizeObserver(resize).observe(host);
    resize();
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
  }

  function resize() {
    const w = host.clientWidth, h = host.clientHeight || 320;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function show(gltf, refit) {
    if (group) scene.remove(group);
    group = new THREE.Group();
    gltf.scene.traverse((o) => {
      if (o.isMesh) {
        o.geometry.computeVertexNormals();
        group.add(new THREE.Mesh(o.geometry, body));
        group.add(new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 28), ink));
      }
    });
    group.rotation.x = -Math.PI / 2;            // CAD is Z-up, three.js is Y-up
    scene.add(group);
    const box = new THREE.Box3().setFromObject(group);
    const c = box.getCenter(new THREE.Vector3()), r = box.getSize(new THREE.Vector3()).length() / 2;
    if (refit || !fitted) {
      controls.target.copy(c);
      camera.position.copy(c).add(new THREE.Vector3(1.1, 0.9, 1.3).normalize().multiplyScalar(r * 3.2));
      camera.near = r / 50; camera.far = r * 50; camera.updateProjectionMatrix();
      fitted = true;
    }
  }

  return {
    reset() { fitted = false; },
    async load(src, refit = false) {
      if (!renderer) init();
      const gltf = typeof src === "string" ? await loader.loadAsync(src)
        : await new Promise((ok, bad) => loader.parse(src, "", ok, bad));
      show(gltf, refit);
    },
    clear() { if (group && scene) { scene.remove(group); group = null; } },
  };
})();

// ------------------------------------------------------------------ timeline
const timeline = {
  start(t0Label = "+0.0") { $("#timeline").innerHTML = ""; this.active = null; },
  add(t, title, cls = "active", body = null) {
    if (this.active) this.active.classList.replace("active", "done");
    const li = el("li", { class: `step ${cls}` }, el("span", { class: "t", text: `+${(t || 0).toFixed(1)}` }),
      el("div", {}, el("b", { text: title }), body));
    $("#timeline").append(li);
    li.scrollIntoView({ block: "nearest" });
    this.active = cls === "active" ? li : null;
    return li;
  },
  finishActive() { if (this.active) this.active.classList.replace("active", "done"); this.active = null; },
  // a step that ran in parallel with the active one: appended without finishing it
  addAside(t, title, cls, body) {
    const li = el("li", { class: `step ${cls}` }, el("span", { class: "t", text: `+${(t || 0).toFixed(1)}` }),
      el("div", {}, el("b", { text: title }), body));
    $("#timeline").append(li);
    li.scrollIntoView({ block: "nearest" });
    return li;
  },
};

function p(text, cls) { return el("p", cls ? { class: cls } : {}, text); }
function list(items, cls) { return el("ul", {}, ...items.map((i) => el("li", { class: cls || "", text: i }))); }

// "callout '64' (distance ...) measures 54 on the part, expected 64" -> plain words
function plain(issue) {
  const m = /^callout '(.+?)' \((.+?)\) measures (.+?) on the part, expected (.+)$/.exec(issue.message);
  if (m) return `${m[1]}: the part has ${m[3]} mm where the drawing says ${m[4]} (${m[2]}).`;
  const m2 = /^callout '(.+?)' \((.+?)\): (.+)$/.exec(issue.message);
  if (m2) return `${m2[1]} (${m2[2]}): ${m2[3]}.`;
  let s = issue.message.replace(/\s*\((high|medium|low)\)$/, "");
  if (issue.source === "compile") s = "The spec did not build: " + s.replace(/^spec[^:]*: (Value error, )?/, "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function short(issue) {
  const s = plain(issue).replace(/^[a-z_0-9]+: /i, "");
  return s.length > 110 ? s.slice(0, 107) + "…" : s.replace(/\.$/, "");
}

// ------------------------------------------------------------------ event handling
function setStatus(s) { $("#tb-status").textContent = s; }

function fileUrl(rel) { return current.base + rel; }

function onEvent(ev) {
  const t = ev.t || 0;
  switch (ev.event) {
    case "read_started":
      setStatus("reading");
      timeline.add(t, current.kind === "text" ? "Reading the description" : "Reading the drawing",
        "active", p("Muse Spark turns it into a part spec with named parameters, and lists every dimension it reads."));
      break;
    case "spec": {
      current.spec = ev.spec;
      if (ev.iteration === 0 && ev.changelog === null) {
        const n = Object.keys(ev.spec?.parameters || {}).length, c = (ev.callouts || []).length;
        timeline.active && timeline.active.querySelector("p")?.replaceWith(
          p(`Read ${n} parameters and ${c} dimensions${ev.call ? ` in ${fmt(ev.call.seconds)} s` : ""}.`));
        $("#tb-part").textContent = ev.spec?.name || "—";
      } else if (ev.iteration > 0) {
        timeline.finishActive();
        const rows = [p(ev.changelog || "Updated the spec.")];
        const kept = (ev.disputes || []).filter((d) => d.status === "rejected");
        if (kept.length) rows.push(p(`Kept ${kept.length} measured dimension(s): the correction disputed them without support from the critic.`));
        timeline.add(t, `Correction ${ev.iteration}`, "done", el("div", {}, ...rows));
      }
      break;
    }
    case "compiled": {
      setStatus("built");
      if (ev.ok) {
        const s = ev.metrics.bbox.size;
        $("#tb-size").textContent = `${fmt(s[0])} × ${fmt(s[1])} × ${fmt(s[2])}`;
        timeline.add(t, ev.iteration === 0 ? "Part built" : `Part rebuilt (iteration ${ev.iteration})`, "done",
          p(`${fmt(s[0])} × ${fmt(s[1])} × ${fmt(s[2])} mm, ${fmt(ev.metrics.volume_mm3 / 1000, 1)} cm³, ` +
            `${ev.metrics.features.length} features. Built by our own compiler (CadQuery), no model involved.`));
        const f = ev.files || {};
        current.lastFiles = f;
        $("#iter-label").textContent = ev.iteration ? `iteration ${ev.iteration}` : "first read";
        if (f["part.glb"]) viewer.load(fileUrl(f["part.glb"]), ev.iteration === 0).catch(console.error);
        if (f["sheet.png"]) showSheet(fileUrl(f["sheet.png"]));
      } else {
        timeline.add(t, "Spec did not build", "fix", list((ev.errors || []).map((m) => plain({ message: m, source: "compile" })), "issue"));
        break;
      }
      timeline.add(t, "Checking dimensions", "active", p("Measuring every dimension from the drawing on the built part."));
      break;
    }
    case "shape_check":
      shapeStep(ev, t);
      break;
    case "checks": {
      setStatus("checking");
      const rows = ev.callouts || [];
      const ok = rows.filter((r) => r.status === "ok").length;
      const checked = rows.filter((r) => ["ok", "mismatch", "error"].includes(r.status)).length;
      const callIssues = (ev.issues || []).filter((i) => i.source === "callout");
      const chips = el("div", { class: "chips" }, ...rows.filter((r) => r.status !== "unchecked" && r.status !== "invalid")
        .map((r) => el("span", { class: `chip ${r.status === "ok" ? "" : "bad"}`, text: r.label })));
      if (timeline.active && timeline.active.querySelector("b").textContent === "Checking dimensions") {
        timeline.active.querySelector("p").replaceWith(el("div", {},
          p(rows.length ? `${ok} of ${checked} dimensions match the part.` : "No dimensions to check."), chips,
          callIssues.length ? list(callIssues.map(plain), "issue") : null));
        if (callIssues.length) timeline.active.classList.add("fix");
        timeline.finishActive();
      }
      const critIssues = (ev.issues || []).filter((i) => i.source === "critic");
      if (ev.critic) {
        timeline.add(t, "Critic compared drawing and part", critIssues.length ? "fix" : "done",
          critIssues.length ? list(critIssues.map(plain), "issue")
            : p("A second model looked at your drawing and the drawing of the built part: no shape differences."));
      } else if (ev.critic_skipped) {
        timeline.add(t, "Critic skipped", "done", p(/shape/.test(ev.critic_skipped)
          ? "The shape check failed, so Talos fixes that first." : "Dimensions are off, so Talos fixes those first."));
      } else if (ev.critic_error) {
        timeline.add(t, "Critic unavailable", "done", p("The visual check failed; the dimension checks still count."));
      }
      if (ev.clean) timeline.add(t, "Verified", "done", p("Every dimension matches and the critic agrees."));
      break;
    }
    case "correction_started":
      setStatus("correcting");
      timeline.add(t, "Correcting", "active", p("Sending the drawing, the spec and what is wrong back to the model."));
      break;
    case "error":
      timeline.add(t, "Problem", "fix", p(ev.message));
      break;
    case "done":
      finish(ev);
      break;
  }
}

// SAM 3.1 shape check: the straightened sheet (first time), then per view the drawing's outline and the comparison
function thumb(rel, caption, cls = "") {
  if (!rel) return null;
  const url = fileUrl(rel);
  return el("figure", { class: `shape-fig ${cls}` }, el("a", { href: url, target: "_blank", rel: "noopener" },
    el("img", { src: url, alt: caption, loading: "lazy" })), el("figcaption", { text: caption }));
}

function shapeStep(ev, t) {
  const title = "Shape check (SAM 3.1)";
  if (!ev.available) {
    if (ev.iteration === 0) timeline.addAside(t, title, "done", p(`Skipped: ${ev.note || "no usable view outline"}.`, "muted"));
    return;
  }
  const body = el("div", {});
  const files = ev.files || {};
  if (ev.iteration === 0 && ev.sheet?.rectified && files.sheet_found && files.sheet_rectified) {
    body.append(el("div", { class: "shape-row" }, thumb(files.sheet_found, "SAM found the sheet"),
      el("span", { class: "shape-arrow", text: "→" }), thumb(files.sheet_rectified, "straightened")));
  }
  const row = el("div", { class: "shape-row" });
  for (const v of ev.views || []) {
    if (v.status === "unavailable") { row.append(el("span", { class: "muted", text: `${v.view}: ${v.note}` })); continue; }
    const mark = v.status === "issue" ? "✗" : "✓";
    if (ev.iteration === 0) row.append(thumb(v.files?.input, `${v.view} outline`));
    row.append(thumb(v.files?.compare, `${v.view} ${mark}`, v.status === "issue" ? "bad" : ""));
  }
  body.append(row, p("Blue: the drawing's outline found by SAM 3.1. Red: the built part's silhouette.", "fine"));
  const issues = ev.issues || [];
  body.append(issues.length ? list(issues.map(plain), "issue")
    : p("Every view's outline in the drawing matches the part: same orientation and proportions."));
  if ((ev.advisories || []).length) body.append(el("details", { class: "fine" },
    el("summary", { text: `Holes: ${ev.advisories.length} note(s), advisory` }), list(ev.advisories.map((m) => plain({ message: m })))));
  timeline.addAside(t, title, issues.length ? "fix" : "done", body);
}

function finish(done) {
  timeline.finishActive();
  const words = {
    pass: "Verified", stuck: "Stopped: the same issue came back", max_iters: "Stopped after the maximum corrections",
    disputed: "Stopped: the model rejected the remaining issues", oscillating: "Stopped: the checks went back and forth",
    budget: "Stopped: budget", error: "Stopped: error",
  };
  setStatus(done.status === "pass" ? "verified" : done.status);
  const rem = done.remaining_issues || [];
  if (done.status !== "pass") {
    timeline.add(done.t, words[done.status] || done.status, "done",
      el("div", {}, p(done.note || "Showing the best version Talos found."), rem.length ? list(rem.map((m) => plain({ message: m })), "issue") : null));
  }
  // what Talos caught and fixed, from the first check that found problems
  const firstChecks = current.events.find((e) => e.event === "checks" && (e.issues || []).length);
  const firstCompile = current.events.find((e) => e.event === "compiled" && !e.ok);
  const caught = firstCompile ? { source: "compile", message: firstCompile.errors[0] } : firstChecks?.issues?.[0];
  if (done.status === "pass" && done.iterations > 0 && caught) {
    showBanner("CAUGHT", `${short(caught)}. Fixed in ${done.iterations} correction${done.iterations > 1 ? "s" : ""}.`, false);
  } else if (done.status === "pass") {
    showBanner("VERIFIED", `Right on the first read. ${done.seconds ? `${fmt(done.seconds)} s` : ""}`.trim(), true);
  }
  if (done.spec) setSpec(done.spec);
  // downloads: files of the version Talos returned
  const compiled = current.events.filter((e) => e.event === "compiled" && e.ok && e.files);
  const fin = compiled.find((e) => e.iteration === done.final_iteration) || compiled[compiled.length - 1];
  finalFiles = fin ? fin.files : null;
  setDownloads(finalFiles && finalFiles["part.step"] ? fileUrl(finalFiles["part.step"]) : null,
    finalFiles && finalFiles["part.stl"] ? fileUrl(finalFiles["part.stl"]) : null, done.spec);
  if (fin && fin.iteration !== current.events.filter((e) => e.event === "compiled").slice(-1)[0]?.iteration) {
    viewer.load(fileUrl(fin.files["part.glb"])).catch(console.error);
  }
  if (current.mode === "replay") $("#run-actions").hidden = !API;
}

function showBanner(stamp, text, info) {
  const b = $("#banner");
  b.innerHTML = "";
  b.className = "banner" + (info ? " info" : "");
  b.append(el("span", { class: "stamp", text: stamp }), el("span", { text }));
  b.hidden = false;
}

function showSheet(url) {
  const v = $("#sheet-view");
  v.innerHTML = "";
  v.append(el("img", { src: url, alt: "Front, side, top and iso views of the built part, labelled with its sizes" }));
}

function showInput(src, prompt) {
  const v = $("#input-view");
  v.innerHTML = "";
  if (prompt) v.append(el("p", { class: "prompt-text", text: prompt }));
  else v.append(el("img", { src, alt: "The input drawing" }));
}

// ------------------------------------------------------------------ parameters
let overrides = {}, compileSeq = 0, compileTimer = null;

function setSpec(spec) {
  current.spec = spec;
  overrides = {};
  const box = $("#params");
  box.innerHTML = "";
  $("#param-errors").hidden = true;
  const params = Object.entries(spec.parameters || {});
  if (!params.length) { box.append(el("p", { class: "empty", text: "This part has no named parameters." })); return; }
  for (const [name, prm] of params) {
    const row = el("div", { class: "param" });
    const label = el("label", { for: `p-${name}` }, name, el("small", { text: prm.description || "" }));
    row.append(label);
    if (typeof prm.value !== "number") {
      row.append(el("span", { class: "expr", text: `= ${prm.value}` }));
      box.append(row);
      continue;
    }
    const v = prm.value;
    const lo = prm.min ?? (v > 0 ? +(v * 0.5).toFixed(2) : v - 10);
    const hi = prm.max ?? (v > 0 ? +(v * 1.5).toFixed(2) : v + 10);
    const step = (hi - lo) <= 20 ? 0.1 : 0.5;
    const range = el("input", { type: "range", min: lo, max: hi, step, value: v, "aria-label": name });
    const num = el("input", { type: "number", id: `p-${name}`, step: "any", value: v });
    const set = (x, from) => {
      if (from !== range) range.value = x;
      if (from !== num) num.value = x;
      row.classList.toggle("changed", Number(x) !== v);
      if (Number(x) === v) delete overrides[name]; else overrides[name] = Number(x);
      scheduleCompile();
    };
    range.addEventListener("input", () => set(range.value, range));
    num.addEventListener("change", () => num.value !== "" && set(num.value, num));
    row.append(range, num);
    box.append(row);
  }
}

function scheduleCompile() {
  clearTimeout(compileTimer);
  compileTimer = setTimeout(recompile, 220);
}

async function recompile() {
  const errBox = $("#param-errors");
  if (!API) { errBox.textContent = "Rebuilding needs the backend, which is not configured on this page."; errBox.hidden = false; return; }
  const seq = ++compileSeq, mine = session;
  setStatus("rebuilding");
  try {
    const r = await fetch(`${API}/compile`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: current.spec, overrides }) });
    const out = await r.json();
    if (seq !== compileSeq || mine !== session) return;           // a newer edit or run took over
    if (!r.ok) throw new Error(out.message || out.detail || r.statusText);
    if (!out.ok) {
      errBox.textContent = out.errors.join(" ");
      errBox.hidden = false;
      setStatus("invalid");
      return;
    }
    errBox.hidden = true;
    const bin = Uint8Array.from(atob(out.glb_b64), (c) => c.charCodeAt(0));
    await viewer.load(bin.buffer);
    const s = out.metrics.bbox.size;
    $("#tb-size").textContent = `${fmt(s[0])} × ${fmt(s[1])} × ${fmt(s[2])}`;
    setStatus(`rebuilt in ${Math.round(out.seconds * 1000)} ms`);
    const spec = structuredClone(current.spec);
    for (const [k, v] of Object.entries(overrides)) spec.parameters[k].value = v;
    setDownloads(`${API}${out.files["part.step"]}`, `${API}${out.files["part.stl"]}`, spec);
  } catch (e) {
    if (seq !== compileSeq) return;
    errBox.textContent = `Could not rebuild: ${e.message}`;
    errBox.hidden = false;
  }
}

function setDownloads(step, stl, spec) {
  const name = (spec?.name || "part").replace(/[^a-z0-9_-]+/gi, "_");
  for (const [id, url, file] of [["#dl-step", step, `${name}.step`], ["#dl-stl", stl, `${name}.stl`]]) {
    const a = $(id);
    if (url) { a.href = url; a.setAttribute("download", file); a.setAttribute("aria-disabled", "false"); }
    else { a.removeAttribute("href"); a.setAttribute("aria-disabled", "true"); }
  }
  const a = $("#dl-spec");
  if (spec) {
    a.href = URL.createObjectURL(new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" }));
    a.setAttribute("download", `${name}.json`);
    a.setAttribute("aria-disabled", "false");
  }
}

// ------------------------------------------------------------------ runs and replays
function begin(opts) {
  session++;
  if (current?.es) current.es.close();
  current = { events: [], lastId: -1, ...opts };
  $("#banner").hidden = true;
  $("#run-actions").hidden = true;
  $("#tb-part").textContent = "—";
  $("#tb-size").textContent = "—";
  setStatus(opts.mode === "replay" ? "replaying" : "starting");
  $("#iter-label").textContent = "";
  $("#params").innerHTML = '<p class="empty">Parameters of the part appear here.</p>';
  $("#sheet-view").innerHTML = '<p class="empty">What Talos compares with your drawing.</p>';
  setDownloads(null, null, null);
  ["#dl-spec"].forEach((id) => $(id).setAttribute("aria-disabled", "true"));
  viewer.reset();
  viewer.clear();
  timeline.start();
  $("#run-mode").textContent = opts.mode === "replay"
    ? `replay of a recorded run · ${opts.model || ""}` : opts.mock ? "live · mock models" : "live";
  document.querySelectorAll(".gallery button").forEach((b) => b.classList.toggle("active", b.dataset.id === opts.id));
}

function push(ev) {
  current.events.push(ev);
  try { onEvent(ev); } catch (e) { console.error(e, ev); }
}

async function replay(entry) {
  begin({ mode: "replay", id: entry.id, base: `gallery/${entry.id}/`, model: entry.model, entry, kind: "image" });
  const mine = session;
  showInput(`gallery/${entry.id}/${entry.input}`);
  const events = await (await fetch(`gallery/${entry.id}/events.json`)).json();
  let prevT = 0;
  for (const ev of events) {
    if (mine !== session) return;
    const gap = Math.min(Math.max(((ev.t || prevT) - prevT) * 0.06, 0.12), 1.0);
    prevT = ev.t || prevT;
    await new Promise((r) => setTimeout(r, gap * 1000));
    if (mine !== session) return;
    push(ev);
  }
}

async function startLive(form, preview) {
  if (!API) { showBanner("OFFLINE", "Live runs need the backend, which is not configured on this page. Try an example.", true); return; }
  const isText = form.has("prompt");
  begin({ mode: "live", kind: isText ? "text" : "image" });
  const mine = session;
  showInput(preview, isText ? form.get("prompt") : null);
  timeline.add(0, "Uploading", "active", p(isText ? "Sending the description." : "Sending the drawing (resized, metadata removed)."));
  let r, out;
  try {
    r = await fetch(`${API}/runs`, { method: "POST", body: form });
    out = await r.json();
  } catch (e) {
    timeline.add(0, "Could not reach the server", "fix", p(e.message));
    setStatus("offline");
    return;
  }
  if (mine !== session) return;
  if (r.status === 429) { showBanner("BUSY", out.message, true); timeline.add(0, "Busy", "done", p(out.message)); setStatus("busy"); return; }
  if (!r.ok) { timeline.add(0, "Upload refused", "fix", p(out.detail || out.message || r.statusText)); setStatus("error"); return; }
  timeline.start();
  current.base = `${API}/runs/${out.run_id}/files/`;
  current.mock = out.mock;
  $("#run-mode").textContent = out.mock ? "live · mock models" : "live · Muse Spark";
  const es = new EventSource(`${API}/runs/${out.run_id}/events`);
  current.es = es;
  es.addEventListener("talos", (m) => {
    const id = Number(m.lastEventId);
    if (mine !== session || id <= current.lastId) return;     // reconnects replay from Last-Event-ID; skip duplicates
    current.lastId = id;
    push(JSON.parse(m.data));
  });
  es.addEventListener("end", () => es.close());
}

// ------------------------------------------------------------------ inputs
function submitFile(file) {
  if (!file) return;
  const form = new FormData();
  form.append("image", file, file.name || "drawing.jpg");
  startLive(form, URL.createObjectURL(file));
}

$("#file").addEventListener("change", (e) => submitFile(e.target.files[0]));
$("#camera").addEventListener("change", (e) => submitFile(e.target.files[0]));
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => submitFile(e.dataTransfer.files[0]));
$("#text-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#prompt").value.trim();
  if (!text) return;
  const form = new FormData();
  form.append("prompt", text);
  startLive(form, null);
});
$("#run-live").addEventListener("click", async () => {
  const entry = current?.entry;
  if (!entry) return;
  const blob = await (await fetch(`gallery/${entry.id}/${entry.input}`)).blob();
  const form = new FormData();
  form.append("image", blob, entry.input);
  startLive(form, `gallery/${entry.id}/${entry.input}`);
});
$("#sheet-view").addEventListener("click", (e) => { if (e.currentTarget.querySelector("img")) e.currentTarget.classList.toggle("zoom"); });

// ------------------------------------------------------------------ gallery
(async () => {
  const items = await (await fetch("gallery/index.json")).json();
  const ul = $("#gallery");
  for (const it of items) {
    const btn = el("button", { type: "button", "data-id": it.id },
      el("img", { src: `gallery/${it.id}/${it.input}`, alt: "", loading: "lazy" }),
      el("span", { class: "cap" }, el("b", { text: it.title }),
        it.corrections ? el("span", { class: "fix", text: "caught & fixed" }) : el("span", { class: "replay", text: "replay" })));
    btn.addEventListener("click", () => replay(it));
    ul.append(el("li", {}, btn));
  }
  const first = items.find((i) => i.corrections) || items[0];
  const params = new URLSearchParams(location.search);
  if (params.get("example")) replay(items.find((i) => i.id === params.get("example")) || first);
})();
