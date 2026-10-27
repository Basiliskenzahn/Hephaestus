"""Talos: read -> compile -> verify -> correct, until the part matches the drawing.

    for event in run("drawing.png", out_dir="out/run1"):
        print(event["event"])

Each iteration builds the current spec, checks it (compile errors, callouts measured on the part, visual critic) and,
if anything is wrong, asks the model for a corrected spec. Stops when all checks pass, after `max_iters` corrections,
when the same issues come back twice in a row, when a correction returns the spec unchanged ("disputed"), or
when it returns to an earlier version ("oscillating"). The critic is sampled twice; see critic.consensus. Events are JSON-serialisable dicts with an "event" key:
  read_started, spec, compiled, checks, correction_started, error, done
The final spec is the best version seen (fewest issues), not necessarily the last.

CLI: python -m hephaestus.talos drawing.png --out out/run1/ [--max-iters 4] [--max-usd 0.5]
"""
import argparse
import json
import os
import sys
import time

from . import critic as critic_mod
from . import measure, meta_api
from .compile import SpecError, compile, write_outputs
from .drawing import render_sheet
from .prompts import CORRECT_PROMPT, READ_PROMPT, correction_request
from .spec import CorrectionOutput, ReadOutput, json_schema


def _call_info(info):
    u = info["usage"]
    return {"cost_usd": round(info["cost_usd"], 6), "seconds": info["seconds"],
            "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}


def build(spec, out_dir=None):
    """Compile one spec version. Returns (compiled event fields, Result or None, sheet PNG or None, compile issues)."""
    try:
        result = compile(spec)
    except SpecError as e:
        return {"ok": False, "errors": e.errors}, None, None, [{"source": "compile", "message": m} for m in e.errors]
    files = {}
    sheet = render_sheet(result)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        for name in write_outputs(result, out_dir):
            files[name] = os.path.join(out_dir, name)
        with open(os.path.join(out_dir, "sheet.png"), "wb") as f:
            f.write(sheet)
        files["sheet.png"] = os.path.join(out_dir, "sheet.png")
    return {"ok": True, "metrics": result.metrics, "warnings": result.warnings, "files": files}, result, sheet, []


def verify(result, sheet, callouts, drawing, client=None, use_critic=True, critic_samples=2):
    """Callout check and visual critic on a compiled part. Returns (checks event fields, issues)."""
    rows = measure.check(callouts, result)
    issues = [{"source": "callout", "message": m} for m in measure.issues(rows)]
    crit = call = None
    if use_critic and client is not None:
        crit, info = critic_mod.consensus(client, drawing, sheet, samples=critic_samples)
        call = _call_info(info)
        for i in critic_mod.blocking(crit):
            who = f"{i['feature_id']}: " if i.get("feature_id") else ""
            issues.append({"source": "critic", "message": f"{who}{i['issue']} ({i['severity']})"})
    advice = critic_mod.advisories(crit) if crit else []
    return {"issues": issues, "advisories": advice, "callouts": rows, "critic": crit, "critic_call": call}, issues


def check(spec, callouts, drawing, client=None, out_dir=None, use_critic=True):
    """build + verify in one go (for evaluation scripts). Returns (compiled fields, checks fields, issues)."""
    compiled, result, sheet, issues = build(spec, out_dir)
    if result is None:
        return compiled, {"issues": issues, "callouts": [], "critic": None, "critic_call": None}, issues
    checks, issues = verify(result, sheet, callouts, drawing, client, use_critic)
    return compiled, checks, issues


def _fingerprint(spec):
    """What the geometry depends on: features and parameter values (not names' descriptions or notes)."""
    if not isinstance(spec, dict):
        return json.dumps(spec)
    params = {k: (v or {}).get("value") for k, v in (spec.get("parameters") or {}).items()}
    return json.dumps({"features": spec.get("features"), "parameters": params}, sort_keys=True)


def run(drawing, max_iters=4, client=None, out_dir=None, start=None, use_critic=True):
    """Generator of events. `start` = {"spec", "callouts"} skips the read and begins at the check (for evaluation)."""
    client = client or meta_api.Client()
    t0, iteration, history, best, seen = time.time(), 0, [], None, []
    spent0 = client.budget.spent

    def ev(name, **kw):
        return {"event": name, "iteration": iteration, "t": round(time.time() - t0, 1), **kw}

    def finish(status, note=None):
        final = best or {"iteration": iteration, "spec": spec, "callouts": callouts, "n_issues": None}
        return ev("done", status=status, note=note, final_iteration=final["iteration"], spec=final["spec"],
                  callouts=final["callouts"], remaining_issues=final.get("issues"), iterations=iteration,
                  corrections=iteration, cost_usd=round(client.budget.spent - spent0, 6),
                  seconds=round(time.time() - t0, 1))

    spec = callouts = None
    try:
        if start is None:
            yield ev("read_started")
            out, info = client.respond(READ_PROMPT, [meta_api.text("Convert this drawing into the part spec and "
                                                                   "list its dimensions."), meta_api.image(drawing)],
                                       json_schema(ReadOutput), "ReadOutput")
            spec, callouts = out.get("spec"), out.get("callouts", [])
            yield ev("spec", spec=spec, callouts=callouts, changelog=None, call=_call_info(info))
        else:
            spec, callouts = start["spec"], start.get("callouts", [])
            yield ev("spec", spec=spec, callouts=callouts, changelog="(given)", call=None)

        while True:
            idir = os.path.join(out_dir, f"iter_{iteration}") if out_dir else None
            if idir:
                os.makedirs(idir, exist_ok=True)
                with open(os.path.join(idir, "spec.json"), "w") as f:
                    json.dump({"spec": spec, "callouts": callouts}, f, indent=2)
            compiled, result, sheet, issues = build(spec, idir)
            yield ev("compiled", **compiled)
            if result is None:
                checks = {"issues": issues, "callouts": [], "critic": None, "critic_call": None}
            else:
                checks, issues = verify(result, sheet, callouts, drawing, client, use_critic)
            clean = not issues
            yield ev("checks", clean=clean, **checks)
            score = len(issues) + (100 if not compiled["ok"] else 0)
            if best is None or score < best["score"]:
                best = {"iteration": iteration, "spec": spec, "callouts": callouts, "score": score,
                        "issues": [i["message"] for i in issues]}
            if clean:
                yield finish("pass")
                return
            key = sorted(i["message"] for i in issues)
            if history and history[-1] == key:
                yield finish("stuck", "the same issues came back after a correction")
                return
            history.append(key)
            if iteration >= max_iters:
                yield finish("max_iters")
                return
            yield ev("correction_started", issues=issues)
            content = [meta_api.text(correction_request(spec, callouts, issues)), meta_api.image(drawing)]
            out, info = client.respond(CORRECT_PROMPT, content, json_schema(CorrectionOutput), "CorrectionOutput")
            iteration += 1
            seen.append(_fingerprint(spec))
            new_fp = _fingerprint(out.get("spec"))
            unchanged, revisits = new_fp == seen[-1], new_fp in seen[:-1]
            spec, callouts = out.get("spec"), out.get("callouts", callouts)
            yield ev("spec", spec=spec, callouts=callouts, changelog=out.get("changelog"), call=_call_info(info))
            if unchanged:
                # the corrector rejected every remaining issue; another round would only re-roll the critic
                yield finish("disputed", out.get("changelog"))
                return
            if revisits:
                # back to an earlier version: the checks are flip-flopping, keep the best version seen
                yield finish("oscillating", "a correction returned to an earlier version")
                return
    except meta_api.BudgetExceeded as e:
        yield ev("error", message=str(e))
        yield finish("budget", str(e))
    except meta_api.ModelError as e:
        yield ev("error", message=str(e))
        yield finish("error", str(e))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m hephaestus.talos")
    ap.add_argument("drawing")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-iters", type=int, default=4, help="maximum correction calls")
    ap.add_argument("--max-usd", type=float, default=0.5)
    ap.add_argument("--model", default=meta_api.DEFAULT_MODEL)
    ap.add_argument("--no-critic", action="store_true")
    args = ap.parse_args(argv)
    client = meta_api.Client(args.model, budget=meta_api.Budget(args.max_usd))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "events.jsonl"), "w") as log:
        for e in run(args.drawing, args.max_iters, client, args.out, use_critic=not args.no_critic):
            log.write(json.dumps(e) + "\n")
            log.flush()
            summary = {"spec": lambda: e.get("changelog") or "", "checks": lambda: f"{len(e['issues'])} issues",
                       "done": lambda: f"{e['status']} after {e['iterations']} corrections, ${e['cost_usd']:.4f}, "
                                       f"{e['seconds']}s"}.get(e["event"], lambda: "")()
            print(f"[{e['t']:6.1f}s] iter {e['iteration']} {e['event']} {summary}", flush=True)
            if e["event"] == "checks":
                for i in e["issues"]:
                    print(f"      - [{i['source']}] {i['message']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
