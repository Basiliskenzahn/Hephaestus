import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

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
  let renderer, labels, scene, camera, controls, group, dims, host, fitted = false, showDims = false, waiting = null;
  const loader = new GLTFLoader();
  const ink = new THREE.LineBasicMaterial({ color: 0x1d2733 });
  const red = new THREE.LineBasicMaterial({ color: 0xd4441c });
  const body = new THREE.MeshStandardMaterial({ color: 0xdfe3e7, roughness: 0.78, metalness: 0.04, flatShading: true,
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });

  function init() {
    host = $("#viewer");
    host.innerHTML = "";
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0xffffff, 0);
    labels = new CSS2DRenderer();
    labels.domElement.className = "labels";
    host.append(renderer.domElement, labels.domElement,
      el("span", { class: "hint", text: matchMedia("(pointer: coarse)").matches
        ? "drag to rotate · pinch to zoom" : "drag to rotate · scroll to zoom · right-drag to pan" }));
    scene = new THREE.Scene();
    // soft studio light: sky/ground fill, a key light and a weaker rim from behind
    scene.add(new THREE.HemisphereLight(0xffffff, 0x9aa1a8, 1.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(1.2, 2, 1.6);
    const rim = new THREE.DirectionalLight(0xffffff, 0.55);
    rim.position.set(-1.5, 0.8, -1.2);
    scene.add(key, rim);
    camera = new THREE.PerspectiveCamera(30, 1, 0.1, 5000);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    new ResizeObserver(resize).observe(host);
    resize();
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); labels.render(scene, camera); });
  }

  function resize() {
    const w = host.clientWidth, h = host.clientHeight || 320;
    renderer.setSize(w, h, false);
    labels.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function fit() {
    const box = new THREE.Box3().setFromObject(group);
    const c = box.getCenter(new THREE.Vector3()), r = box.getSize(new THREE.Vector3()).length() / 2;
    controls.target.copy(c);
    const dist = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * (camera.aspect < 1 ? 1.35 / camera.aspect ** 0.5 : 1.1);
    camera.position.copy(c).add(new THREE.Vector3(1.0, 0.75, 1.25).normalize().multiplyScalar(dist));
    camera.near = r / 50; camera.far = r * 60; camera.updateProjectionMatrix();
    controls.update();
  }

  // overall dimensions as drawing-style lines with labels (CAD X, Y, Z; three.js is Y-up, CAD Y runs along -z)
  function dimensions() {
    if (dims) { dims.traverse((o) => o.isCSS2DObject && o.element.remove()); scene.remove(dims); dims = null; }
    if (!showDims || !group) return;
    const b = new THREE.Box3().setFromObject(group), s = b.getSize(new THREE.Vector3());
    const pad = Math.max(s.x, s.y, s.z) * 0.12, V = (x, y, z) => new THREE.Vector3(x, y, z);
    dims = new THREE.Group();
    const line = (a, z, ext) => {
      const pts = [...ext.flatMap(([p, q]) => [p, q]), a, z];
      dims.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), red));
      const lab = el("span", { class: "dim-label" });
      const o = new CSS2DObject(lab);
      o.position.copy(a.clone().add(z).multiplyScalar(0.5));
      dims.add(o);
      return lab;
    };
    const y0 = b.min.y, zf = b.max.z, xr = b.max.x;
    line(V(b.min.x, y0, zf + pad), V(b.max.x, y0, zf + pad),
      [[V(b.min.x, y0, zf), V(b.min.x, y0, zf + pad * 1.2)], [V(b.max.x, y0, zf), V(b.max.x, y0, zf + pad * 1.2)]]).textContent = `${fmt(s.x)} mm`;
    line(V(xr + pad, y0, b.min.z), V(xr + pad, y0, zf),
      [[V(xr, y0, b.min.z), V(xr + pad * 1.2, y0, b.min.z)], [V(xr, y0, zf), V(xr + pad * 1.2, y0, zf)]]).textContent = `${fmt(s.z)} mm`;
    line(V(xr + pad, b.min.y, zf + pad), V(xr + pad, b.max.y, zf + pad),
      [[V(xr, b.max.y, zf), V(xr + pad * 1.2, b.max.y, zf + pad * 1.2)]]).textContent = `${fmt(s.y)} mm`;
    scene.add(dims);
  }

  function show(gltf, refit) {
    if (group) scene.remove(group);
    if (waiting) { waiting.remove(); waiting = null; }
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
    if (refit || !fitted) { fit(); fitted = true; }
    dimensions();
    $("#view-reset").disabled = false;
    $("#dims-toggle").disabled = false;
  }

  $("#view-reset").addEventListener("click", () => group && fit());
  $("#dims-toggle").addEventListener("click", (e) => {
    showDims = !showDims;
    e.currentTarget.setAttribute("aria-pressed", String(showDims));
    dimensions();
  });

  return {
    reset() { fitted = false; },
    async load(src, refit = false) {
      if (!renderer) init();
      const gltf = typeof src === "string" ? await loader.loadAsync(src)
        : await new Promise((ok, bad) => loader.parse(src, "", ok, bad));
      show(gltf, refit);
    },
    clear() {
      if (group && scene) { scene.remove(group); group = null; }
      if (dims) dimensions();
      $("#view-reset").disabled = true;
      $("#dims-toggle").disabled = true;
    },
    // while the first read runs: a placeholder instead of an empty box
    waiting(text) {
      if (!renderer) init();
      if (waiting) waiting.remove();
      waiting = text ? el("div", { class: "building", text }) : null;
      if (waiting) host.append(waiting);
    },
  };
})();

// ------------------------------------------------------------------ timeline
// One step per thing Talos does: start time (m:ss), what happened in plain words, and how long it took. Raw model
// details (ids, tokens, cost) only show with the "details" toggle.
const clockText = (t) => `${Math.floor((t || 0) / 60)}:${String(Math.floor((t || 0) % 60)).padStart(2, "0")}`;
const timeline = {
  start() { $("#timeline").innerHTML = ""; this.active = null; },
  _li(t, title, cls, body, dur) {
    const li = el("li", { class: `step ${cls}` }, el("span", { class: "t", text: clockText(t) }),
      el("div", {}, el("b", { text: title }), body), el("span", { class: "dur", text: dur || "" }));
    li.dataset.t = t || 0;
    const ol = $("#timeline");
    ol.append(li);
    ol.scrollTop = ol.scrollHeight;          // scroll the timeline box only, never the page
    return li;
  },
  close(li, t) {
    li.classList.replace("active", "done");
    const d = (t || 0) - Number(li.dataset.t);
    const span = li.querySelector(".dur");
    if (span && !span.textContent && d >= 0.05) span.textContent = `${d < 10 ? d.toFixed(1) : Math.round(d)} s`;
  },
  add(t, title, cls = "active", body = null) {
    if (this.active) this.close(this.active, t);
    const li = this._li(t, title, cls, body, cls === "active" ? "…" : "");
    if (cls === "active") li.querySelector(".dur").textContent = "";
    this.active = cls === "active" ? li : null;
    return li;
  },
  finishActive(t) { if (this.active) this.close(this.active, t ?? Number(this.active.dataset.t)); this.active = null; },
  // a step that ran in parallel with the active one (shape check, number re-read): its own duration, if known
  addAside(t, title, cls, body, seconds) {
    return this._li(t, title, cls, body, seconds ? `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s` : "");
  },
  // the moments to remember: "Caught: … Fixed.", "Checked the numbers", "Verified"
  moment(li, stamp, ok = false) {
    li.classList.add("moment");
    if (ok) li.classList.add("ok");
    if (stamp) li.querySelector("b").append(el("span", { class: "stamp-sm", text: stamp }));
    return li;
  },
};

// elapsed time while a run is going; replays show the recorded time of each event
const clock = {
  timer: null, t0: 0,
  start(live) {
    this.stop();
    const c = $("#clock");
    c.hidden = false;
    c.classList.remove("stopped");
    c.textContent = "0:00";
    if (live) {
      this.t0 = performance.now();
      this.timer = setInterval(() => { c.textContent = clockText((performance.now() - this.t0) / 1000); }, 250);
    }
  },
  show(t) { if (!this.timer) $("#clock").textContent = clockText(t); },
  stop(t) {
    clearInterval(this.timer);
    this.timer = null;
    const c = $("#clock");
    if (t != null) c.textContent = clockText(t);
    c.classList.add("stopped");
  },
};
$("#raw-toggle").addEventListener("change", (e) => $("#timeline").classList.toggle("show-raw", e.target.checked));
const raw = (text) => (text ? el("div", { class: "raw", text }) : null);
const callRaw = (c) => c && raw(`${c.model || "model"} · effort ${c.effort || "?"} · ${c.input_tokens ?? "?"} in / ` +
  `${c.output_tokens ?? "?"} out tokens · $${(c.cost_usd || 0).toFixed(4)} · ${fmt(c.seconds || 0)} s`);

function p(text, cls) { return el("p", cls ? { class: cls } : {}, text); }
function list(items, cls) { return el("ul", {}, ...items.map((i) => el("li", { class: cls || "", text: i }))); }

// "callout '64' (distance ...) measures 54 on the part, expected 64" -> plain words, no ids
function plain(issue) {
  const m = /^callout '(.+?)' \((.+?)\) measures (.+?) on the part, expected (.+)$/.exec(issue.message);
  if (m) return `${m[1]}: the part has ${m[3]} mm where the drawing says ${m[4]}.`;
  const m2 = /^callout '(.+?)' \((.+?)\): (.+)$/.exec(issue.message);
  if (m2) return `${m2[1]}: ${m2[3].replace(/'[a-z_0-9[\]]+'/gi, "a feature")}.`;
  let s = issue.message.replace(/\s*\((high|medium|low)\)$/, "").replace(/^[a-z_0-9[\]]+: /i, "");
  if (issue.source === "compile") s = "The spec did not build: " + s.replace(/^spec[^:]*: (Value error, )?/, "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function short(issue) {
  const s = plain(issue);
  return s.length > 110 ? s.slice(0, 107) + "…" : s.replace(/\.$/, "");
}

// issues as a list: plain words, the raw message under "details"
function issueList(issues, cls = "issue") {
  return el("ul", {}, ...issues.map((i) => el("li", { class: cls }, plain(i), raw(i.message))));
}

// ------------------------------------------------------------------ event handling
function setStatus(s, live = false) {
  const d = $("#tb-status");
  d.textContent = s;
  d.classList.toggle("live", live);
}

function fileUrl(rel) { return current.base + rel; }

function onEvent(ev) {
  handle(ev);
  if ((ev.event === "checks" || ev.event === "shape_check") && current.uncertain) markStrength();
}

const RUNNING = { read_started: "Reading your drawing (Muse Spark)…", compiled: "Part built, checking it against your drawing…",
  correction_started: "Something is off: sending it back to Muse Spark for a fix…" };
function handle(ev) {
  const t = ev.t || 0;
  if (RUNNING[ev.event] && $("#banner").classList.contains("running")) $("#banner span:last-child").textContent = RUNNING[ev.event];
  clock.show(t);
  switch (ev.event) {
    case "read_started":
      setStatus("reading", true);
      timeline.add(t, current.kind === "text" ? "Reading your description" : "Reading your drawing",
        "active", p("Muse Spark turns it into a part spec with named parameters, and lists every dimension it reads."));
      viewer.waiting("Muse Spark is reading the drawing.\nThe part appears as soon as it compiles.");
      break;
    case "spec": {
      current.spec = ev.spec;
      if (ev.iteration === 0 && ev.changelog === null) {
        const n = Object.keys(ev.spec?.parameters || {}).length, c = (ev.callouts || []).length;
        timeline.active && timeline.active.querySelector("p")?.replaceWith(
          el("div", {}, p(`Read ${n} parameters and ${c} dimensions.`), callRaw(ev.call)));
        $("#tb-part").textContent = (ev.spec?.name || "—").replace(/_/g, " ");
      } else if (ev.iteration > 0) {
        timeline.finishActive(t);
        const rows = [p(ev.changelog || "Updated the spec."), callRaw(ev.call)];
        const kept = (ev.disputes || []).filter((d) => d.status === "rejected");
        if (kept.length) rows.push(p(`Kept ${kept.length} measured dimension(s): the correction disputed them without support from the critic.`));
        timeline.add(t, "Fixed", "done", el("div", {}, ...rows));
      }
      break;
    }
    case "compiled": {
      setStatus("built", true);
      if (ev.ok) {
        const s = ev.metrics.bbox.size;
        $("#tb-size").textContent = `${fmt(s[0])} × ${fmt(s[1])} × ${fmt(s[2])}`;
        timeline.add(t, ev.iteration === 0 ? "Part built" : "Part rebuilt", "done",
          p(`${fmt(s[0])} × ${fmt(s[1])} × ${fmt(s[2])} mm, ${ev.metrics.features.length} features. ` +
            "Built by our own compiler (CadQuery), no model involved."));
        const f = ev.files || {};
        current.lastFiles = f;
        $("#iter-label").textContent = ev.iteration ? `after ${ev.iteration} fix${ev.iteration > 1 ? "es" : ""}` : "first read";
        if (f["part.glb"]) viewer.load(fileUrl(f["part.glb"]), ev.iteration === 0).catch(console.error);
        if (f["sheet.png"]) showSheet(fileUrl(f["sheet.png"]));
      } else {
        timeline.add(t, "The spec did not build", "fix", issueList((ev.errors || []).map((m) => ({ message: m, source: "compile" }))));
        break;
      }
      timeline.add(t, "Checking every dimension", "active", p("Measuring each dimension from the drawing on the built part."));
      break;
    }
    case "shape_check":
      shapeStep(ev, t);
      break;
    case "numbers":
      numbersStep(ev, t);
      break;
    case "rejected":
      timeline.add(t, "Can’t build a part from this", "fix", el("div", {}, p(ev.message), callRaw(ev.call)));
      setStatus("needs input");
      break;
    case "checks": {
      setStatus("checking", true);
      const rows = ev.callouts || [];
      const ok = rows.filter((r) => r.status === "ok").length;
      const checked = rows.filter((r) => ["ok", "mismatch", "error"].includes(r.status)).length;
      const conflicts = rows.filter((r) => r.status === "conflict");
      const callIssues = (ev.issues || []).filter((i) => i.source === "callout");
      const chips = el("div", { class: "chips" }, ...rows.filter((r) => !["unchecked", "invalid"].includes(r.status))
        .map((r) => el("span", { class: `chip ${r.status === "ok" ? "" : r.status === "conflict" ? "conflict" : "bad"}`,
          title: r.status === "conflict" ? r.conflict || "contradicts another number" : r.status, text: r.label })));
      if (timeline.active && /^Checking every dimension/.test(timeline.active.querySelector("b").textContent)) {
        const li = timeline.active;
        li.querySelector("p").replaceWith(el("div", {},
          p(rows.length ? `${ok} of ${checked} dimensions match the part.` : "No dimensions to check."), chips,
          conflicts.length ? p(`${conflicts.length} number${conflicts.length > 1 ? "s contradict" : " contradicts"} another one on the drawing: built with the best guess, you pick (red numbers on the drawing).`) : null,
          callIssues.length ? issueList(callIssues) : null));
        li.querySelector("b").textContent = callIssues.length ? "Dimensions: something is off" : "Dimensions checked";
        if (callIssues.length) timeline.moment(li, "CAUGHT");
        timeline.finishActive(t);
        li.querySelector(".dur").textContent = "";     // the checks event also waits for the second look
      }
      const critIssues = (ev.issues || []).filter((i) => i.source === "critic");
      const critT = ev.critic_call?.seconds;
      if (ev.critic) {
        const li = timeline.addAside(t, critIssues.length ? "Second look: shape differs" : "Second look: no differences",
          critIssues.length ? "fix" : "done", el("div", {}, critIssues.length ? issueList(critIssues)
            : p("A separate Muse Spark call compared your drawing with a drawing of the built part."), callRaw(ev.critic_call)), critT);
        if (critIssues.length) timeline.moment(li, "CAUGHT");
      } else if (ev.critic_skipped) {
        timeline.addAside(t, "Second look skipped", "done", p(/shape/.test(ev.critic_skipped)
          ? "The outline check found a problem, so Talos fixes that first." : "Dimensions are off, so Talos fixes those first.", "muted"));
      } else if (ev.critic_error) {
        timeline.addAside(t, "Second look unavailable", "done", p("The visual check failed; the measured checks still count.", "muted"));
      }
      if (ev.clean) {
        const shapeOk = current.events.some((e) => e.event === "shape_check" && e.iteration === ev.iteration && e.available);
        const what = [rows.length ? "every dimension matches" : null, shapeOk ? "the outlines match (SAM 3.1)" : null]
          .filter(Boolean).join(", ") || "the part built";
        const critic = ev.critic ? " and the second look agrees"
          : ev.critic_error ? "; the second look was unavailable" : "";
        current.verifiedStep = timeline.moment(timeline.addAside(t, "Verified", "done",
          p(what.charAt(0).toUpperCase() + what.slice(1) + critic + ".")), "", true);
      }
      break;
    }
    case "correction_started": {
      setStatus("correcting", true);
      const n = (ev.issues || []).length;
      timeline.add(t, "Correcting", "active", p(`Sending the drawing, the spec and the ${n > 1 ? `${n} problems` : "problem"} back to Muse Spark.`));
      break;
    }
    case "error":
      timeline.add(t, "Problem", "fix", el("div", {}, p(friendlyError(ev.message)), raw(ev.message)));
      break;
    case "done":
      finish(ev);
      break;
  }
}

// ------------------------------------------------------------------ the numbers, checked twice
// Each written number is read again from a crop of where it is written, with nothing else in view. Chips sit on the
// input image: confirmed ones are subtle, uncertain ones offer both readings and a field; a choice sets the
// parameter and rebuilds (no model call). A number the read itself found contradictory is shown the same way.
// A re-read disagreement alone is a soft "confirm?"; red only when another signal backs the other value: the read
// itself flagged a conflict with a specific alternative, the callout check measured a mismatch, or the shape check
// found that view's proportions off along this number's axis (same rule as reread.strength in Python).
const VIEW_AXES = { top: ["x", "y"], front: ["x", "z"], side: ["y", "z"] };
function strengthOf(n) {
  if (n.status !== "uncertain") return null;
  if (n.conflict && n.second != null) return "red";
  const callout = current.events.find((e) => e.event === "spec" && e.iteration === 0)?.callouts?.[n.index];
  const row = current.events.find((e) => e.event === "checks" && e.iteration === 0)?.callouts?.[n.index];
  if (row && row.status === "mismatch") return "red";
  const shape = current.events.find((e) => e.event === "shape_check" && e.iteration === 0);
  if (callout?.measure === "overall" && callout.axis && shape?.available) {
    for (const v of shape.views || []) {
      const flagged = [...(v.advisories || []), ...(v.issues || [])].some((m) => /outline is/.test(m));
      if (flagged && (VIEW_AXES[v.view] || []).includes(callout.axis)) return "red";
    }
  }
  return "soft";
}

function markStrength() {
  let red = 0;
  for (const chip of document.querySelectorAll("#input-view .chip-num.uncertain")) {
    const s = strengthOf(chip._n);
    chip.classList.toggle("red", s === "red");
    red += s === "red";
  }
  current.red = red;
}

function numbersStep(ev, t) {
  if (!ev.available) return;
  const located = (ev.numbers || []).filter((n) => n.text_box && n.status !== "not_located");
  const unc = located.filter((n) => n.status === "uncertain");
  current.uncertain = unc.length;
  const li = timeline.addAside(t, unc.length ? `Checked the numbers: ${unc.length} to confirm`
    : `Checked the numbers: all ${located.length} read the same twice`, unc.length ? "fix" : "done",
    el("div", {}, p(unc.length ? "Each number was read a second time on its own. Where the two readings differ, tap the number on your drawing and pick the right value."
      : "Each number was read a second time on its own, from a crop of where it is written."),
    unc.length ? list(unc.map((n) => n.conflict ? `${n.label}: ${n.conflict}`
      : `${n.label}: read as ${fmt(n.value)}, second reading ${n.second == null ? "unclear" : fmt(n.second)}`)) : null,
    raw(`${(ev.calls || []).map((c) => `${c.crops} crops, ${c.model || ""}`).join("; ")} · $${(ev.cost_usd || 0).toFixed(4)}`)),
    ev.seconds);
  timeline.moment(li, unc.length ? "CHECK" : "", !unc.length);
  const layer = $("#input-view .chips-layer");
  if (!layer) return;
  layer.innerHTML = "";
  for (const n of located) {
    const [x1, y1, x2, y2] = n.text_box;
    const chip = el("button", { type: "button", class: `chip-num ${n.status}`, title: n.status === "uncertain"
      ? (n.conflict || `Read as ${fmt(n.value)}, re-read as ${n.second == null ? "unclear" : fmt(n.second)}. Tap to confirm.`) : `${n.label}: ${n.status}`,
      style: `left:${Math.min(x1, x2) / 10}%;top:${Math.min(y1, y2) / 10}%` }, fmt(n.value));
    chip._n = n;
    if (n.status === "uncertain") chip.addEventListener("click", (e) => { e.stopPropagation(); chooser(chip, n); });
    else chip.tabIndex = -1;
    layer.append(chip);
  }
  markStrength();
}

function chooser(chip, n) {
  document.querySelectorAll(".chip-pop").forEach((x) => x.remove());
  const pop = el("div", { class: "chip-pop", role: "dialog", "aria-label": `Which number is ${n.label}?` });
  pop.append(el("span", { class: "pop-note", text: n.conflict ? `Conflict: ${n.conflict}` : "Which is it?" }));
  const pick = (v) => { pop.remove(); confirmNumber(chip, n, v); };
  for (const v of [n.value, n.second].filter((v, i, a) => v != null && a.indexOf(v) === i)) {
    const b = el("button", { type: "button", class: "btn" }, fmt(v));
    b.addEventListener("click", () => pick(v));
    pop.append(b);
  }
  const inp = el("input", { type: "number", step: "any", inputmode: "decimal", placeholder: "other", "aria-label": `value for ${n.label}` });
  const ok = el("button", { type: "button", class: "btn" }, "OK");
  ok.addEventListener("click", () => inp.value !== "" && pick(Number(inp.value)));
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && inp.value !== "") pick(Number(inp.value)); });
  pop.append(inp, ok);
  chip.after(pop);
  const left = parseFloat(chip.style.left), top = parseFloat(chip.style.top);
  pop.style.left = left > 55 ? "auto" : chip.style.left;
  if (left > 55) pop.style.right = `${Math.max(0, 100 - left - 8)}%`;
  pop.style[top > 60 ? "bottom" : "top"] = top > 60 ? `calc(${100 - top}% + 6px)` : `calc(${chip.style.top} + 26px)`;
  pop.querySelector(".btn").focus();
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".chip-pop") && !e.target.closest(".chip-num")) document.querySelectorAll(".chip-pop").forEach((x) => x.remove());
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll(".chip-pop").forEach((x) => x.remove()); });

async function confirmNumber(chip, n, v) {
  chip.classList.remove("uncertain");
  chip.classList.add("by-user");
  chip.textContent = fmt(v);
  const ok = v === n.value ? true : await editor.setNumber(n, v);   // the first reading was right: nothing to change
  if (!ok) { chip.classList.add("uncertain"); chip.classList.remove("by-user"); chip.textContent = fmt(n.value); return; }
  current.uncertain = Math.max(0, (current.uncertain || 0) - 1);
  chip.classList.remove("red");
  if (!current.uncertain && $("#banner .stamp")?.textContent === "CHECK") {
    showBanner("CONFIRMED", "Every flagged number is confirmed by you. The part is rebuilt with your values.", true);
  }
}

// SAM 3.1 shape check: the straightened sheet (first time), then per view the drawing's outline and the comparison
function thumb(rel, caption, cls = "") {
  if (!rel) return null;
  const url = fileUrl(rel);
  return el("figure", { class: `shape-fig ${cls}` }, el("a", { href: url, target: "_blank", rel: "noopener" },
    el("img", { src: url, alt: caption, loading: "lazy", width: 120, height: 76 })), el("figcaption", { text: caption }));
}

function shapeStep(ev, t) {
  const sec = ev.sam?.seconds;
  if (!ev.available) {
    if (ev.iteration === 0) timeline.addAside(t, "Outline check skipped", "done", el("div", {}, p("No usable view outline in this drawing; the other checks still count.", "muted"), raw(ev.note)));
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
    if (v.status === "unavailable") { row.append(el("span", { class: "muted", text: `${v.view}: no outline` }), raw(v.note)); continue; }
    const mark = v.status === "issue" ? "✗" : "✓";
    if (ev.iteration === 0) row.append(thumb(v.files?.input, `${v.view} outline`));
    row.append(thumb(v.files?.compare, `${v.view} ${mark}`, v.status === "issue" ? "bad" : ""));
  }
  body.append(row, p("Blue: your drawing's outline, found by SAM 3.1. Red: the built part's silhouette.", "fine"));
  const issues = ev.issues || [];
  body.append(issues.length ? issueList(issues)
    : p("Every view's outline matches the part: same orientation and proportions."));
  const li = timeline.addAside(t, issues.length ? "Outlines: the part is mirrored or turned" : "Outlines match (SAM 3.1)",
    issues.length ? "fix" : "done", body, sec);
  if (issues.length) timeline.moment(li, "CAUGHT");
}

// what went wrong, in words a user can act on; the raw message stays available under "details"
function friendlyError(msg) {
  const m = String(msg || "");
  if (/budget|daily spend cap/i.test(m)) return "Today’s budget for live runs is used up, so Talos stopped here. The examples still replay; live runs resume tomorrow (UTC).";
  if (/timed? ?out|Timeout|Read timed out|stream/i.test(m)) return "The model took too long to answer. Talos kept the best version so far; try again in a minute.";
  if (/not JSON|no output text|malformed|invalid/i.test(m)) return "The model returned an answer Talos could not use. Talos kept the best version so far; try again.";
  if (/HTTP 5\d\d|HTTP 429|overloaded|rate/i.test(m)) return "The model service is busy right now. Try again in a minute.";
  return "Something went wrong while talking to the model. Try again, or pick an example.";
}

// a replay runs about 10x faster than the recorded run: say so while it plays
function replayPill(text) {
  for (const id of ["#replay-pill", "#replay-pill-3d"]) {
    const x = $(id);
    x.hidden = !text;
    x.textContent = text || "";
    x.classList.toggle("playing", /×/.test(text || ""));
  }
}

function finish(done) {
  if (current.mode === "replay") replayPill("REPLAY");
  timeline.finishActive(done.t);
  clock.stop(done.seconds ?? done.t);
  viewer.waiting(null);
  const words = {
    pass: "Verified", stuck: "Stopped: the same issue came back", max_iters: "Stopped after the maximum corrections",
    disputed: "Stopped: the model rejected the remaining issues", oscillating: "Stopped: the checks went back and forth",
    unverified: "Measured checks pass; one note to check by eye",
    budget: "Stopped: today’s budget is used up", error: "Stopped: the model did not answer properly",
  };
  setStatus(done.status === "pass" ? "verified" : done.status === "unverified" ? "built, 1 note"
    : done.status === "rejected" ? "needs input" : done.status.replace(/_/g, " "));
  if (done.status === "rejected") {
    showBanner("CHECK THE INPUT", done.note || "Talos cannot build a part from this input.", true);
    if (current.mode === "replay") $("#run-actions").hidden = !API;
    return;
  }
  const rem = done.remaining_issues || [];
  if (done.status === "unverified") {
    const notes = done.unverified_notes || [];
    timeline.add(done.t, words.unverified, "done", el("div", {},
      p("Every dimension and outline check passes. The second look raised the note below; Talos tried one correction, " +
        "which did not hold up, so it kept the measured part."),
      notes.length ? issueList(notes.map((m) => ({ message: m })), "note") : null));
  } else if (done.status !== "pass") {
    const note = ["error", "budget"].includes(done.status) ? friendlyError(done.note) : done.note;
    const shown = done.spec ? "Showing the best version Talos found." : "No part could be built.";
    timeline.add(done.t, words[done.status] || done.status, "done",
      el("div", {}, p(note || shown), done.spec && note ? p(shown, "fine") : null,
        rem.length ? issueList(rem.map((m) => ({ message: m }))) : null));
  }
  // what Talos caught and fixed, from the first check that found problems
  const firstChecks = current.events.find((e) => e.event === "checks" && (e.issues || []).length);
  const firstCompile = current.events.find((e) => e.event === "compiled" && !e.ok);
  const caught = firstCompile ? { source: "compile", message: firstCompile.errors[0] } : firstChecks?.issues?.[0];
  // run time, except for replays recorded with the slower development model (the live model takes ~40 s)
  const took = done.seconds && !/contributor/.test(current.model || "") ? ` ${fmt(done.seconds, 0)} s.` : "";
  if (current.uncertain) markStrength();
  if (current.uncertain) {
    const n = current.uncertain, red = current.red || 0;
    showBanner("CHECK", `Built and checked. ${n} number${n > 1 ? "s" : ""} on your drawing ${n > 1 ? "were" : "was"} read two ` +
      `ways${red ? ` (${red} also contradicted by the part)` : ""}: tap ${n > 1 ? "them" : "it"} to confirm.`, !red);
    if (current.verifiedStep) current.verifiedStep.querySelector("b").firstChild.textContent = "Verified against the numbers as first read";
  } else if (done.status === "pass" && done.iterations > 0 && caught) {
    showBanner("CAUGHT", `${short(caught)}. Fixed in ${done.iterations} correction${done.iterations > 1 ? "s" : ""}.${took}`, false);
    const fixed = [...document.querySelectorAll("#timeline .step")].filter((li) => li.querySelector("b").textContent.startsWith("Fixed"));
    for (const li of fixed) timeline.moment(li, "FIXED", true);
  } else if (done.status === "pass") {
    showBanner("VERIFIED", `Right on the first read.${took}`, true);
  }
  const built = current.events.some((e) => e.event === "compiled" && e.ok);
  if (done.spec && built) {
    setSpec(done.spec);
    editor.ready(done);
  } else if (done.spec) {
    showBanner("NO PART", "The spec never compiled, so there is no part to show. The timeline lists why; try a clearer drawing or describe the part in words.", true);
  }
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
  if ($("#banner").classList.contains("running")) {
    showBanner(done.status === "pass" ? "VERIFIED" : "STOPPED", done.status === "pass" ? "Verified." : `${words[done.status] || done.status}. The timeline says why.`, true);
  }
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
  else v.append(el("div", { class: "input-wrap" }, el("img", { src, alt: "The input drawing" }), el("div", { class: "chips-layer" })));
}

// ------------------------------------------------------------------ parameters
let overrides = {}, compileSeq = 0, compileTimer = null;

// parameters grouped by the first feature that uses them ("Overall" for the base body), labelled with their
// descriptions and units
function paramGroups(spec) {
  const names = Object.keys(spec.parameters || {});
  const uses = (obj, name) => new RegExp(`(^|[^a-zA-Z0-9_])${name}([^a-zA-Z0-9_]|$)`).test(JSON.stringify(obj));
  const groups = new Map();
  for (const name of names) {
    const ft = (spec.features || []).find((f) => uses({ ...f, id: undefined }, name));
    const key = !ft ? "Other" : ft === spec.features[0] ? "Overall" : ft.id.replace(/_/g, " ");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(name);
  }
  return groups;
}

function unitOf(name, prm) {
  const d = `${name} ${prm.description || ""}`.toLowerCase();
  if (/angle|deg|°|slope|tilt/.test(d)) return "°";
  if (/count|number of|^n_|_n$|qty|holes? count/.test(d) && Number.isInteger(prm.value)) return "";
  return "mm";
}

function setSpec(spec) {
  current.spec = spec;
  overrides = {};
  const box = $("#params");
  box.innerHTML = "";
  $("#param-errors").hidden = true;
  const params = spec.parameters || {};
  if (!Object.keys(params).length) { box.append(el("p", { class: "empty", text: "This part has no named parameters." })); return; }
  for (const [group, names] of paramGroups(spec)) {
    const g = el("div", { class: "pgroup" }, el("h3", { text: group }));
    for (const name of names) g.append(paramRow(name, params[name]));
    box.append(g);
  }
}

function paramRow(name, prm) {
  const row = el("div", { class: "param" });
  const desc = (prm.description || "").replace(/^assumed:\s*/i, "assumed: ");
  row.append(el("label", { for: `p-${name}` }, desc ? desc.charAt(0).toUpperCase() + desc.slice(1) : name,
    el("span", { class: "pname", text: name })));
  if (typeof prm.value !== "number") {
    row.append(el("span", { class: "expr", text: `= ${prm.value}` }));
    return row;
  }
  const v = prm.value, unit = unitOf(name, prm);
  const lo = prm.min ?? (v > 0 ? +(v * 0.5).toFixed(2) : v - 10);
  const hi = prm.max ?? (v > 0 ? +(v * 1.5).toFixed(2) : v + 10);
  const step = (hi - lo) <= 20 ? 0.1 : 0.5;
  const range = el("input", { type: "range", min: lo, max: hi, step, value: v, "aria-label": `${name}${unit ? ` (${unit})` : ""}` });
  const num = el("input", { type: "number", id: `p-${name}`, step: "any", value: v, inputmode: "decimal" });
  let before = null;
  const set = (x, from) => {
    if (from !== range) range.value = x;
    if (from !== num) num.value = x;
    row.classList.toggle("changed", Number(x) !== v);
    if (Number(x) === v) delete overrides[name]; else overrides[name] = Number(x);
    scheduleCompile();
  };
  // every finished drag or typed value can be undone
  const begin = () => { if (!before) before = editor.snapshot(); };
  const commit = () => { if (before) editor.record(before, `${name} → ${num.value}${unit ? " " + unit : ""}`); before = null; };
  range.addEventListener("pointerdown", begin);
  range.addEventListener("keydown", begin);
  range.addEventListener("input", () => { begin(); set(range.value, range); });
  range.addEventListener("change", commit);
  num.addEventListener("change", () => { if (num.value === "") return; begin(); set(num.value, num); commit(); });
  row.append(range, el("span", { class: "val" }, num, unit ? el("span", { class: "unit", text: unit }) : null));
  return row;
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
  showBanner(opts.mode === "replay" ? "REPLAY" : "RUNNING", opts.mode === "replay"
    ? `${opts.entry?.title || "Example"}: Talos reads the drawing, builds the part and checks it.` : "Starting…", true);
  $("#banner").classList.add("running");
  $("#run-actions").hidden = true;
  $("#tb-part").textContent = "—";
  $("#tb-size").textContent = "—";
  setStatus(opts.mode === "replay" ? "replaying" : "starting", true);
  $("#iter-label").textContent = "";
  $("#params").innerHTML = '<p class="empty">Parameters of the part appear here.</p>';
  $("#sheet-view").innerHTML = '<p class="empty">What Talos compares with your drawing.</p>';
  setDownloads(null, null, null);
  ["#dl-spec"].forEach((id) => $(id).setAttribute("aria-disabled", "true"));
  editor.reset();
  viewer.reset();
  viewer.clear();
  timeline.start();
  clock.start(opts.mode === "live");
  replayPill(opts.mode === "replay" ? "REPLAY ×10" : null);
  current.uncertain = 0;
  current.verifiedStep = null;
  $("#run-mode").textContent = opts.mode === "replay"
    ? `Replay of a recorded run (${/contributor/.test(opts.model || "") ? "development model" : "Muse Spark 1.3"}), about 10× faster.` : "Live run.";
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
  if (entry.note) timeline.addAside(0, "About this example", "done", p(entry.note, "fine"));
  const events = await (await fetch(`gallery/${entry.id}/events.json`)).json();
  let prevT = 0;
  for (const ev of events) {
    if (mine !== session) return;
    // about 10x faster than the recorded run, but slow enough to follow each step
    const gap = Math.min(Math.max(((ev.t || prevT) - prevT) * 0.1, 0.4), 1.8);
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
  if (narrow()) $(".views").scrollIntoView({ block: "start" });
  showInput(preview, isText ? form.get("prompt") : null);
  timeline.add(0, "Uploading", "active", p(isText ? "Sending the description." : "Sending the drawing (resized, metadata removed)."));
  let r, out;
  try {
    r = await fetch(`${API}/runs`, { method: "POST", body: form });
    out = await r.json();
  } catch (e) {
    showBanner("OFFLINE", "The backend did not answer. Try again, or pick an example.", true);
    timeline.add(0, "Could not reach the server", "fix", el("div", {}, p("The backend did not answer. Check your connection, or pick an example."), raw(e.message)));
    setStatus("offline");
    clock.stop();
    return;
  }
  if (mine !== session) return;
  if (r.status === 429) { clock.stop(); showBanner("BUSY", out.message, true); timeline.add(0, "Busy", "done", p(out.message)); setStatus("busy"); return; }
  if (!r.ok) { clock.stop(); showBanner("REFUSED", out.detail || out.message || r.statusText, true); timeline.add(0, "Upload refused", "fix", p(out.detail || out.message || r.statusText)); setStatus("error"); return; }
  timeline.start();
  current.base = `${API}/runs/${out.run_id}/files/`;
  current.mock = out.mock;
  $("#run-mode").textContent = out.mock ? "Live run with recorded model answers (mock mode)." : "Live run: Muse Spark 1.3, SAM 3.1.";
  const es = new EventSource(`${API}/runs/${out.run_id}/events`);
  current.es = es;
  es.addEventListener("talos", (m) => {
    const id = Number(m.lastEventId);
    if (mine !== session || id <= current.lastId) return;     // reconnects replay from Last-Event-ID; skip duplicates
    current.lastId = id;
    push(JSON.parse(m.data));
  });
  es.addEventListener("end", () => { current.ended = true; es.close(); });
  // EventSource retries on its own after a dropped connection (resuming from Last-Event-ID); it gives up only when
  // the server answers with an error, e.g. 404 after a restart lost the run
  es.addEventListener("error", () => {
    if (mine !== session || current.ended || es.readyState !== EventSource.CLOSED) return;
    timeline.finishActive();
    timeline.add(current.events.at(-1)?.t || 0, "Connection lost", "fix",
      p("The server lost this run (it may have restarted). Upload the drawing again, or pick an example."));
    setStatus("disconnected");
    showBanner("LOST", "The server lost this run. Upload the drawing again, or pick an example.", true);
    clock.stop();
    viewer.waiting(null);
  });
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
// The page opens on a replay of the run where Talos catches and fixes something (?example=<id> picks another,
// ?autoplay=0 starts empty).
const narrow = () => window.matchMedia("(max-width: 860px)").matches;
(async () => {
  let items = [];
  try { items = await (await fetch("gallery/index.json")).json(); } catch (e) { console.warn("no gallery", e); return; }
  const ul = $("#gallery");
  for (const it of items) {
    const caught = it.corrections ? "caught & fixed" : /^Caught/.test(it.note || "") ? "caught · tap to fix" : null;
    const btn = el("button", { type: "button", "data-id": it.id, title: it.note || it.title },
      el("img", { src: `gallery/${it.id}/${it.input}`, alt: "", loading: "lazy", width: 160, height: 120 }),
      el("span", { class: "cap" }, el("b", { text: it.title }),
        caught ? el("span", { class: "fix", text: caught }) : el("span", { class: "replay", text: "replay" })));
    btn.addEventListener("click", () => {
      replay(it);
      if (narrow()) $(".views").scrollIntoView({ block: "start" });
    });
    ul.append(el("li", {}, btn));
  }
  const params = new URLSearchParams(location.search);
  const first = items.find((i) => i.corrections) || items[0];
  if (params.get("example")) replay(items.find((i) => i.id === params.get("example")) || first);
  else if (params.get("autoplay") !== "0" && first) replay(first);
})();


// ------------------------------------------------------------------ edits in words (typed or spoken)
// The model returns a small structured edit; the server applies it, rebuilds and validates. The part is then
// "edited": the drawing checks no longer cover the changed dimensions, and every edit can be undone.
const editor = (() => {
  let history = [], verified = null, recorder = null, chunks = [], stopTimer = null;
  const status = (html, cls = "") => { const s = $("#edit-status"); s.className = `edit-status ${cls}`; s.innerHTML = html; };
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function liveSpec() {                       // the spec as shown, slider changes included
    const spec = structuredClone(current.spec);
    for (const [k, v] of Object.entries(overrides)) spec.parameters[k].value = v;
    return spec;
  }

  function badge(on) {
    let b = $("#tb-part .edited-badge");
    if (on && !b) $("#tb-part").append(el("span", { class: "edited-badge", text: "edited" }));
    if (!on && b) b.remove();
  }

  async function show(out) {                  // a compiled part from /edit or /compile
    const bin = Uint8Array.from(atob(out.glb_b64), (c) => c.charCodeAt(0));
    await viewer.load(bin.buffer);
    const s = out.metrics.bbox.size;
    $("#tb-size").textContent = `${fmt(s[0])} × ${fmt(s[1])} × ${fmt(s[2])}`;
    setDownloads(`${API}${out.files["part.step"]}`, `${API}${out.files["part.stl"]}`, current.spec);
  }

  async function apply(instruction) {
    if (!instruction || !current?.spec) return;
    const mine = session;
    $("#edit-apply").disabled = true;
    status(`Editing: “${esc(instruction)}”…`);
    setStatus("editing");
    let r, out;
    try {
      r = await fetch(`${API}/edit`, { method: "POST", headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(60000),
        body: JSON.stringify({ spec: liveSpec(), instruction, callouts: verified?.callouts || [], base_spec: verified?.spec }) });
      out = await r.json();
    } catch (e) {
      out = { message: e.name === "TimeoutError" ? "The edit took longer than a minute. Try again." : e.message };
      r = { ok: false };
    }
    $("#edit-apply").disabled = false;
    if (mine !== session) return;
    if (!r.ok) { status(esc(out.message || out.detail || "The edit failed."), "bad"); setStatus("edit failed"); return; }
    if (out.status === "clarification") { status(esc(out.clarification), "ask"); setStatus("question"); return; }
    if (out.status === "error") {
      status(`Could not build that: ${esc((out.errors || []).join(" "))}`, "bad"); setStatus("edit failed"); return;
    }
    history.push({ spec: liveSpec(), label: out.explanation });
    setSpec(out.spec);
    await show(out);
    badge(true);
    $("#edit-undo").disabled = false;
    $("#edit-text").value = "";
    const rows = out.callouts || [];
    const edited = rows.filter((x) => x.status === "edited").length;
    const held = rows.filter((x) => x.status === "ok").length;
    const off = (out.callout_issues || []).length;
    const note = rows.length ? ` ${held} drawing dimension${held === 1 ? "" : "s"} still match${edited ? `, ${edited} changed by you` : ""}${off ? `, ${off} off` : ""}.` : "";
    status(`<b>${esc(out.explanation)}</b> · rebuilt and validated.${note}`, "done");
    setStatus("edited");
    timeline.add(current.events.at(-1)?.t || 0, `Edited: ${out.explanation}`, "done", p(`“${instruction}” → ${(out.changes || []).join(", ")}. ` +
      "The drawing checks no longer cover these dimensions; the build is validated."));
    timeline.active = null;
  }

  async function undo() {
    const prev = history.pop();
    if (!prev) return;
    setSpec(prev.spec);
    $("#edit-undo").disabled = !history.length;
    badge(history.length > 0);
    status(`Undone: ${esc(prev.label)}`);
    const r = await fetch(`${API}/compile`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: prev.spec, overrides: {} }) });
    const out = await r.json();
    if (out.ok) await show(out);
    setStatus(history.length ? "edited" : "as read");
  }

  // ---------- voice: MediaRecorder -> decode -> 16 kHz mono 16-bit WAV (the only format the API takes)
  async function toWav(blob) {
    const ctx = new AudioContext();
    const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
    ctx.close();
    const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(audio.duration * 16000)), 16000);
    const src = off.createBufferSource();
    src.buffer = audio;
    src.connect(off.destination);
    src.start();
    const pcm = (await off.startRendering()).getChannelData(0);
    const v = new DataView(new ArrayBuffer(44 + pcm.length * 2));
    const str = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    str(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVEfmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
    return new Blob([v], { type: "audio/wav" });
  }

  async function micToggle() {
    const btn = $("#mic");
    if (recorder) { recorder.stop(); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { status("No microphone access. Type the edit instead.", "bad"); return; }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      clearTimeout(stopTimer);
      stream.getTracks().forEach((t) => t.stop());
      btn.classList.remove("recording"); btn.querySelector(".mic-label").textContent = "Speak";
      const blob = new Blob(chunks, { type: recorder.mimeType });
      recorder = null;
      status("Transcribing (Muse Voice Transcribe)…");
      try {
        const form = new FormData();
        form.append("audio", await toWav(blob), "edit.wav");
        const t0 = performance.now();
        const r = await fetch(`${API}/transcribe`, { method: "POST", body: form, signal: AbortSignal.timeout(30000) });
        const out = await r.json();
        if (!r.ok) throw new Error(out.message || out.detail || r.statusText);
        if (!out.transcript) { status("Heard nothing. Try again, closer to the microphone.", "bad"); return; }
        $("#edit-text").value = out.transcript;
        $("#edit-text").focus();
        status(`Heard <b>“${esc(out.transcript)}”</b> in ${((performance.now() - t0) / 1000).toFixed(1)} s. ` +
          "Fix it if it is wrong, then Apply.", "heard");
      } catch (e) { status(`Could not transcribe: ${esc(e.message)}`, "bad"); }
    };
    recorder.start();
    btn.classList.add("recording"); btn.querySelector(".mic-label").textContent = "Stop";
    status("Listening… say the change, then press Stop.");
    stopTimer = setTimeout(() => recorder && recorder.stop(), 15000);
  }

  $("#edit-form").addEventListener("submit", (e) => { e.preventDefault(); apply($("#edit-text").value.trim()); });
  $("#edit-undo").addEventListener("click", undo);
  $("#mic").addEventListener("click", micToggle);
  if (!window.MediaRecorder || !navigator.mediaDevices) $("#mic").hidden = true;

  // a number confirmed by the user on the drawing: set its parameter and rebuild, no model call
  async function setNumber(n, v) {
    const spec = liveSpec();
    const prm = n.parameter && spec.parameters?.[n.parameter];
    if (!prm || typeof prm.value !== "number") {
      $("#edit-text").value = `${n.label} should be ${fmt(v)}`;
      status(`“${esc(n.label)}” is not a single parameter. Check the sentence in the box, then Apply.`, "ask");
      return false;
    }
    const r = await fetch(`${API}/compile`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: { ...spec, parameters: { ...spec.parameters, [n.parameter]: { ...prm, value: v } } }, overrides: {} }) });
    const out = await r.json();
    if (!out.ok) { status(`Could not build with ${esc(n.parameter)} = ${fmt(v)}: ${esc((out.errors || []).join(" "))}`, "bad"); return false; }
    history.push({ spec, label: `${n.parameter} ${fmt(prm.value)} → ${fmt(v)}` });
    spec.parameters[n.parameter] = { ...prm, value: v };
    setSpec(spec);
    if (verified?.callouts?.[n.index]) verified.callouts[n.index] = { ...verified.callouts[n.index], value: v, label: String(v) };
    await show(out);
    $("#edit-undo").disabled = false;
    status(`<b>${esc(n.parameter)} ${fmt(prm.value)} → ${fmt(v)}</b> · confirmed on the drawing, rebuilt (no model call).`, "done");
    setStatus("confirmed");
    return true;
  }

  return {
    setNumber,
    snapshot: () => liveSpec(),
    record(spec, label) { history.push({ spec, label }); $("#edit-undo").disabled = false; },
    reset() {
      history = []; verified = null;
      $("#edit-form").hidden = true; $("#edit-undo").disabled = true; $("#edit-text").value = ""; status("");
      badge(false);
    },
    ready(done) {
      if (!API || !done.spec) return;
      verified = { spec: done.spec, callouts: done.callouts || [] };
      $("#edit-form").hidden = false;
      status("Change the part in words: type, or press Speak.");
    },
  };
})();
