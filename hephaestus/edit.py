"""Edits after a part is shown: "make the holes M6", "10 mm thicker", "remove the slot" (typed or transcribed).

One model call (Muse Spark, low effort) turns the instruction and the current spec into a small structured edit
(spec.EditOutput: set_parameters, add_features, remove_features, explanation, or a clarification question). This
module applies it in code, rebuilds the part and validates it. A build error goes back to the model once; after
that the error is shown and the part stays as it was.

The drawing-based checks no longer apply to what the user changed: callouts whose measurement on the edited part
differs from the verified part (`base_spec`) are marked "edited"; only the unchanged ones are still checked.
The shape check and the critic do not run on edited parts.
"""
import copy
import json
import time

from . import measure, meta_api
from .compile import SpecError, compile
from .prompts import EDIT_PROMPT, edit_request
from .spec import EditOutput, json_schema

EDIT_EFFORT = "low"
ADDABLE = {"hole", "slot", "linear_pattern", "circular_pattern", "fillet", "chamfer", "extrude"}


class EditError(ValueError):
    """An edit that cannot be applied to the spec; the message is written as feedback for the model."""


def apply_edit(spec, edit):
    """New spec dict with the edit applied, and a list of what changed. Raises EditError."""
    new = copy.deepcopy(spec)
    params = new.setdefault("parameters", {})
    changes = []
    for p in edit.get("set_parameters") or []:
        name, value = p.get("name"), p.get("value")
        if isinstance(value, str) and name in _names(value):
            raise EditError(f"parameter '{name}' is set to an expression over itself ('{value}'); give the new "
                            f"value as a number")
        if name in params:
            old = params[name].get("value")
            params[name] = {**params[name], "value": value}
            changes.append(f"{name}: {old} -> {value}")
        else:
            params[name] = {"value": value, "description": "added by an edit"}
            changes.append(f"{name}: new, {value}")
    ids = [f["id"] for f in new["features"]]
    remove = list(dict.fromkeys(edit.get("remove_features") or []))
    for fid in remove:
        if fid not in ids:
            raise EditError(f"there is no feature '{fid}' to remove (features: {', '.join(ids)})")
    # patterns built on a removed feature go too, unless the edit adds the base back with the same id
    readded = {f.get("id") for f in edit.get("add_features") or []}
    gone = set(remove)
    for f in new["features"]:
        if f.get("type") in ("linear_pattern", "circular_pattern") and f.get("feature") in gone - readded:
            gone.add(f["id"])
    keep = [f for f in new["features"] if f["id"] not in gone]
    changes += [f"removed {fid}" for fid in ids if fid in gone]
    for f in edit.get("add_features") or []:
        t = f.get("type")
        if t not in ADDABLE or (t == "extrude" and f.get("operation") != "cut"):
            raise EditError(f"feature '{f.get('id')}': an edit can add holes, slots, patterns, fillets, chamfers and "
                            f"pockets (extrude with operation cut), not {t}")
        if f.get("id") in {k["id"] for k in keep}:
            raise EditError(f"feature id '{f.get('id')}' already exists; remove it first to replace it")
        if f.get("id") in gone:
            # replaced in place: keep the original order so later features still see it
            pos = ids.index(f["id"])
            before = sum(1 for k in keep if ids.index(k["id"]) < pos)
            keep.insert(before, f)
            changes.append(f"replaced {f['id']}")
        else:
            keep.append(f)
            changes.append(f"added {f['id']}")
    if not keep:
        raise EditError("the edit removes every feature")
    new["features"] = keep
    return new, changes


def _names(expr):
    import re
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)


def build(spec):
    """Compile for validation (no silhouettes). Returns (Result, None) or (None, error messages)."""
    try:
        return compile(spec, silhouettes=False), None
    except SpecError as e:
        return None, list(e.errors)


def callout_rows(callouts, result, base_result):
    """Callout rows on the edited part. A callout whose measurement differs from the verified part is 'edited'
    (the user changed that dimension); the others keep their status and still count."""
    rows = measure.check(callouts, result)
    base = measure.check(callouts, base_result) if base_result is not None else [None] * len(rows)
    for row, b in zip(rows, base):
        if b is None or row["status"] in ("unchecked", "invalid"):
            continue
        changed = (row.get("measured") is None) != (b.get("measured") is None) or (
            row.get("measured") is not None and abs(row["measured"] - b["measured"]) > measure.TOL)
        if changed or (row["status"] == "error") != (b["status"] == "error"):
            row["status"] = "edited"
            row.pop("message", None)
    return rows


def edit_part(spec, instruction, client, model=None, effort=EDIT_EFFORT, callouts=None, base_spec=None):
    """Apply a natural-language edit. Returns
    {status: applied | clarification | error, spec, explanation, clarification, changes, errors, callouts,
     callout_issues, result (compile Result, not JSON), calls, cost_usd, seconds}."""
    t0 = time.time()
    calls, errors, out = [], None, None
    for attempt in range(2):
        content = [meta_api.text(edit_request(spec, instruction, errors))]
        out, info = client.respond(EDIT_PROMPT, content, json_schema(EditOutput), "EditOutput", model=model,
                                   effort=effort)
        calls.append({"seconds": info["seconds"], "cost_usd": round(info["cost_usd"], 6),
                      "output_tokens": info["usage"].get("output_tokens", 0), "model": info.get("model")})
        base = {"spec": spec, "explanation": out.get("explanation"), "clarification": None, "changes": [],
                "errors": [], "callouts": [], "callout_issues": [], "result": None, "calls": calls}
        if out.get("clarification"):
            return _done({**base, "status": "clarification", "clarification": out["clarification"]}, t0)
        try:
            new, changes = apply_edit(spec, out)
        except EditError as e:
            errors = [str(e)]
            continue
        if not changes:
            errors = ["the edit changes nothing; return the changes the instruction asks for, or a clarification"]
            continue
        result, errors = build(new)
        if result is None:
            continue
        rows = []
        if callouts:
            base_result = build(base_spec or spec)[0]
            rows = callout_rows(callouts, result, base_result)
        return _done({**base, "status": "applied", "spec": new, "changes": changes, "result": result,
                      "callouts": rows, "callout_issues": measure.issues(rows)}, t0)
    return _done({**base, "status": "error", "explanation": (out or {}).get("explanation"), "errors": errors}, t0)


def _done(rec, t0):
    rec["cost_usd"] = round(sum(c["cost_usd"] for c in rec["calls"]), 6)
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def public(rec):
    """The JSON-serialisable part of an edit_part result."""
    return {k: v for k, v in rec.items() if k != "result"}


if __name__ == "__main__":
    import argparse
    import sys
    from .talos import PRESETS
    ap = argparse.ArgumentParser(prog="python -m hephaestus.edit")
    ap.add_argument("spec")
    ap.add_argument("instruction")
    ap.add_argument("--preset", default="dev")
    ap.add_argument("--max-usd", type=float, default=0.05)
    args = ap.parse_args()
    with open(args.spec) as f:
        spec = json.load(f)
    s = PRESETS[args.preset]
    rec = edit_part(spec, args.instruction, meta_api.Client(s.read_model, budget=meta_api.Budget(args.max_usd)),
                    model=s.read_model)
    print(json.dumps(public(rec), indent=1))
    sys.exit(0 if rec["status"] != "error" else 1)
