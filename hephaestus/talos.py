"""Talos: read -> compile -> verify -> correct, until the part matches the drawing.

    for event in run("drawing.png", settings=PRESETS["live"], out_dir="out/run1"):
        print(event["event"])

Each iteration builds the current spec, then checks it: compile errors, then the callouts measured on the part, and
only if those pass, the visual critic (the slow part). Anything wrong goes into one correction call.

Callouts are the contract. They come from the first read and stay fixed: a correction changes the spec, not the
callouts. A correction may dispute a callout it believes was misread. The disputed callout is replaced provisionally
only if the critic consensus had independently flagged the same feature in the check the correction answered, and
the replacement is kept only if the next critic consensus no longer flags that feature. Otherwise the original
callout stays and keeps blocking. Measurements beat the critic.

Stops: pass | max_iters | stuck (same issues twice) | disputed (spec returned unchanged) | oscillating (back to an
earlier version) | budget | error. The returned spec is the best version seen (fewest issues).

Events are JSON-serialisable dicts with an "event" key:
  read_started, spec, compiled, checks, correction_started, error, done

CLI: python -m hephaestus.talos drawing.png --out out/run1/ [--preset live|benchmark] [--max-iters 4] [--max-usd 0.5]
"""
import argparse
import copy
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, replace

from . import critic as critic_mod
from . import measure, meta_api
from .compile import SpecError, compile, write_outputs
from .drawing import render_sheet
from .prompts import CORRECT_PROMPT, READ_PROMPT, correction_request
from .spec import CorrectionOutput, ReadOutput, json_schema


@dataclass(frozen=True)
class Settings:
    """Model settings per role. See PRESETS for the chosen defaults and docs/talos.md for the measurements."""
    read_model: str = meta_api.DEFAULT_MODEL
    read_effort: str = "medium"
    correct_model: str = meta_api.DEFAULT_MODEL
    correct_effort: str = "medium"
    critic_model: str = meta_api.DEFAULT_MODEL
    critic_effort: str = "medium"
    critic_samples: int = 2
    critic_max_tokens: int = 10000
    max_iters: int = 4


# Chosen from experiments/benchmark/results/matrix (photo + rough reads, injected-error critic eval), Oct 2026:
# - muse-spark-1.3 is 2-3x faster than the contributor model in every role.
# - read: medium effort; low halves the time (14 s vs 27 s median) but lost one rough L-bracket.
# - critic: low effort, 2 samples (consensus). Every critic config gave the same loop-level detection (14/16
#   injected errors, callouts catch the rest), and low effort was the fastest (9 s median for one sample, 11 s for
#   two in parallel). One sample flagged more errors on its own, but in the live benchmark a single sample
#   hallucinated a slope kink and the correction made a correct read worse; consensus costs ~2 s and guards that.
#   Low effort peaked at 7.2k output tokens (reasoning included), so the cap is 10k; medium effort hit 8k without
#   answering in 3 of 112 calls.
LIVE = Settings(read_model="muse-spark-1.3", read_effort="medium",
                correct_model="muse-spark-1.3", correct_effort="medium",
                critic_model="muse-spark-1.3", critic_effort="low", critic_samples=2, critic_max_tokens=10000)
PRESETS = {
    # live app: user uploads must not go to training, so no contributor models
    "live": LIVE,
    # the benchmark measures the product, so it runs the live settings
    "benchmark": LIVE,
    # development: contributor models are ~12x cheaper but slower; read effort low matched medium on them
    "dev": Settings(read_effort="low", critic_effort="medium", critic_samples=1, critic_max_tokens=10000),
}


def _call_info(info):
    u = info["usage"]
    return {"cost_usd": round(info["cost_usd"], 6), "seconds": info["seconds"],
            "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
            "model": info.get("model"), "effort": info.get("effort")}


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


def callout_check(result, callouts):
    rows = measure.check(callouts, result)
    return rows, [{"source": "callout", "message": m} for m in measure.issues(rows)]


def run_critic(client, drawing, sheet, settings):
    """Critic consensus with the failure fallback. Returns (critic output or None, call info or None, error or None)."""
    try:
        crit, info = critic_mod.consensus(client, drawing, sheet, samples=settings.critic_samples,
                                          model=settings.critic_model, effort=settings.critic_effort,
                                          max_tokens=settings.critic_max_tokens)
    except meta_api.BudgetExceeded:
        raise
    except meta_api.ModelError as e:
        # a failed critic call should not end the run: carry on with the compile and callout checks
        return None, None, str(e)[:300]
    return crit, _call_info(info), None


def critic_issues(crit):
    out = []
    for i in critic_mod.blocking(crit):
        who = f"{i['feature_id']}: " if i.get("feature_id") else ""
        out.append({"source": "critic", "message": f"{who}{i['issue']} ({i['severity']})"})
    return out


def verify(result, sheet, callouts, drawing, client=None, settings=Settings(), use_critic=True, force_critic=False):
    """Callouts first; the critic only if the callouts pass (or `force_critic`). Returns (checks fields, issues)."""
    rows, issues = callout_check(result, callouts)
    checks = {"callouts": rows, "critic": None, "critic_call": None, "advisories": []}
    if use_critic and client is not None:
        if issues and not force_critic:
            checks["critic_skipped"] = "callouts failed; fixing those first"
        else:
            crit, call, err = run_critic(client, drawing, sheet, settings)
            checks.update(critic=crit, critic_call=call)
            if err:
                checks["critic_error"] = err
            elif crit:
                issues = issues + critic_issues(crit)
                checks["advisories"] = critic_mod.advisories(crit)
    checks["issues"] = issues
    return checks, issues


def check(spec, callouts, drawing, client=None, out_dir=None, use_critic=True, settings=Settings(), force_critic=False):
    """build + verify in one go (for evaluation scripts). Returns (compiled fields, checks fields, issues)."""
    compiled, result, sheet, issues = build(spec, out_dir)
    if result is None:
        return compiled, {"issues": issues, "callouts": [], "critic": None, "critic_call": None}, issues
    checks, issues = verify(result, sheet, callouts, drawing, client, settings, use_critic, force_critic)
    return compiled, checks, issues


def _fingerprint(spec):
    """What the geometry depends on: features and parameter values (not descriptions or notes)."""
    if not isinstance(spec, dict):
        return json.dumps(spec)
    params = {k: (v or {}).get("value") for k, v in (spec.get("parameters") or {}).items()}
    return json.dumps({"features": spec.get("features"), "parameters": params}, sort_keys=True)


def _feature_key(callout):
    return critic_mod._key({"feature_id": (callout or {}).get("feature")})


def run(drawing, max_iters=None, client=None, out_dir=None, start=None, use_critic=True, settings=None):
    """Generator of events. `start` = {"spec", "callouts"} skips the read and begins at the check (for evaluation)."""
    settings = settings or Settings()
    max_iters = settings.max_iters if max_iters is None else max_iters
    client = client or meta_api.Client()
    t0, iteration, history, best, seen = time.time(), 0, [], None, []
    spent = [0.0]           # this run's own calls (the budget may be shared by parallel runs)
    disputes = []           # every dispute raised, with its outcome
    critic_flagged = set()  # features the critic consensus flagged in the latest check

    def ev(name, **kw):
        return {"event": name, "iteration": iteration, "t": round(time.time() - t0, 1), **kw}

    def finish(status, note=None):
        final = best or {"iteration": iteration, "spec": spec, "callouts": callouts}
        return ev("done", status=status, note=note, final_iteration=final["iteration"], spec=final["spec"],
                  callouts=final["callouts"], remaining_issues=final.get("issues"), iterations=iteration,
                  corrections=iteration, disputes=disputes, cost_usd=round(spent[0], 6),
                  seconds=round(time.time() - t0, 1), settings=asdict(settings))

    spec = callouts = original = None
    try:
        if start is None:
            yield ev("read_started")
            out, info = client.respond(READ_PROMPT, [meta_api.text("Convert this drawing into the part spec and "
                                                                   "list its dimensions."), meta_api.image(drawing)],
                                       json_schema(ReadOutput), "ReadOutput", model=settings.read_model,
                                       effort=settings.read_effort)
            spec, callouts = out.get("spec"), out.get("callouts") or []
            spent[0] += info["cost_usd"]
            yield ev("spec", spec=spec, callouts=callouts, changelog=None, call=_call_info(info))
        else:
            spec, callouts = start["spec"], start.get("callouts") or []
            yield ev("spec", spec=spec, callouts=callouts, changelog="(given)", call=None)
        original = copy.deepcopy(callouts)      # the contract
        callouts = copy.deepcopy(callouts)      # effective list: originals + provisionally accepted replacements

        while True:
            idir = os.path.join(out_dir, f"iter_{iteration}") if out_dir else None
            if idir:
                os.makedirs(idir, exist_ok=True)
                with open(os.path.join(idir, "spec.json"), "w") as f:
                    json.dump({"spec": spec, "callouts": callouts}, f, indent=2)
            compiled, result, sheet, issues = build(spec, idir)
            yield ev("compiled", **compiled)      # the UI can show the part before any check runs
            pending = [d for d in disputes if d["status"] == "provisional"]
            if result is None:
                checks = {"issues": issues, "callouts": [], "critic": None, "critic_call": None}
            else:
                checks, issues = verify(result, sheet, callouts, drawing, client, settings, use_critic,
                                        force_critic=bool(pending))
                spent[0] += (checks.get("critic_call") or {}).get("cost_usd", 0)
                crit = checks.get("critic")
                critic_flagged = set(crit.get("agreed_features", [])) if crit else set()
                if pending:
                    # confirm or revert provisional replacements with this (independent) critic run
                    reverted = False
                    for d in pending:
                        if crit is not None and d["feature"] not in critic_flagged:
                            d["status"] = "accepted"
                        else:
                            d["status"] = "rejected"
                            d["outcome"] = ("the critic still flags this feature after the change"
                                            if crit is not None else "no critic result to confirm it")
                            callouts[d["index"]] = copy.deepcopy(original[d["index"]])
                            reverted = True
                    if reverted:
                        rows, callout_iss = callout_check(result, callouts)
                        issues = callout_iss + [i for i in issues if i["source"] != "callout"]
                        checks.update(callouts=rows, issues=issues)
            checks["disputes"] = copy.deepcopy(disputes)
            clean = not issues
            yield ev("checks", clean=clean, **checks)
            score = len(issues) + (100 if not compiled["ok"] else 0)
            if best is None or score < best["score"]:
                best = {"iteration": iteration, "spec": spec, "callouts": copy.deepcopy(callouts), "score": score,
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
            out, info = client.respond(CORRECT_PROMPT, content, json_schema(CorrectionOutput), "CorrectionOutput",
                                       model=settings.correct_model, effort=settings.correct_effort)
            spent[0] += info["cost_usd"]
            iteration += 1
            seen.append(_fingerprint(spec))
            new_fp = _fingerprint(out.get("spec"))
            unchanged, revisits = new_fp == seen[-1], new_fp in seen[:-1]
            spec = out.get("spec")
            raised = []
            for d in out.get("disputed_callouts") or []:
                k = d.get("index")
                if not isinstance(k, int) or not 0 <= k < len(original):
                    continue
                rec = {"index": k, "raised_at": iteration, "reason": d.get("reason"), "original": original[k],
                       "replacement": d.get("replacement"), "feature": _feature_key(original[k])}
                if rec["feature"] and rec["feature"] in critic_flagged and d.get("replacement"):
                    rec["status"] = "provisional"
                    callouts[k] = copy.deepcopy(d["replacement"])
                else:
                    rec["status"] = "rejected"
                    rec["outcome"] = "the critic had not independently flagged this feature"
                disputes.append(rec)
                raised.append(rec)
            yield ev("spec", spec=spec, callouts=callouts, changelog=out.get("changelog"), disputes=raised,
                     call=_call_info(info))
            if unchanged and not any(r["status"] == "provisional" for r in raised):
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
    ap.add_argument("--preset", default="live", choices=sorted(PRESETS))
    ap.add_argument("--max-iters", type=int, default=None, help="maximum correction calls")
    ap.add_argument("--max-usd", type=float, default=0.5)
    ap.add_argument("--no-critic", action="store_true")
    args = ap.parse_args(argv)
    settings = PRESETS[args.preset]
    if args.max_iters is not None:
        settings = replace(settings, max_iters=args.max_iters)
    client = meta_api.Client(settings.read_model, budget=meta_api.Budget(args.max_usd))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "events.jsonl"), "w") as log:
        for e in run(args.drawing, client=client, out_dir=args.out, use_critic=not args.no_critic, settings=settings):
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
