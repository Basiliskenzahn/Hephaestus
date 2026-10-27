"""Visual critic: compare the input drawing with a render of the compiled part. Fresh context, no generator reasoning.

consensus() can sample the critic several times and block only on high-severity issues every sample raises on the
same feature. With the contributor model at medium effort that removed flip-flopping misreadings of photos; with
muse-spark-1.3 at low effort (the live default) a single sample had no false alarms and caught more real errors,
so the presets use one sample. Frozen callouts (talos.py) now guard against a wrong critic instead.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import meta_api
from .spec import json_schema


class CriticIssue(BaseModel):
    feature_id: Optional[str] = Field(None, description="id as labelled in the rendering, or null")
    issue: str = Field(description="what differs, in one sentence, e.g. 'slot runs along Y, drawing shows it along X'")
    severity: Literal["high", "medium", "low"] = Field(
        description="high: wrong or missing shape/feature; medium: wrong position or size; low: minor or unsure")


class CriticOutput(BaseModel):
    matches: bool
    issues: list[CriticIssue]


CRITIC_PROMPT = """You check a CAD part against the engineering drawing it was made from.
Image 1 is the drawing. Image 2 is the part rendered from the CAD model: FRONT, SIDE and TOP views at one scale \
(hidden edges dashed) and an ISO view. Labels in image 2 show overall sizes, feature ids, hole diameters and slot sizes.

List only real differences between the part and the drawing:
- missing or extra features (holes, slots, steps, fillets, chamfers, pockets)
- wrong orientation (e.g. a slot or rib running the wrong way, a feature on the wrong face)
- wrong position or size where the drawing gives it
- a view of the drawing read wrongly (e.g. depth taken as height)

Do not report differences in line style, colour, label placement, view layout, or dimensions the drawing does not give.
Use the feature ids from image 2. If the part matches the drawing, return matches: true and an empty list."""


def critique(client, drawing, sheet_png, model=None, effort=None, max_tokens=10000):
    """Returns (CriticOutput as dict, call info). max_tokens caps reasoning + answer: the critic only lists issues."""
    content = [meta_api.text("Image 1: the drawing."), meta_api.image(drawing),
               meta_api.text("Image 2: the part built from it."), meta_api.image(sheet_png, "image/png")]
    out, info = client.respond(CRITIC_PROMPT, content, json_schema(CriticOutput), "CriticOutput",
                               max_output_tokens=max_tokens, model=model, effort=effort)
    return CriticOutput.model_validate(out).model_dump(), info


def _key(issue):
    m = re.match(r"[A-Za-z][A-Za-z0-9_\-]*", (issue.get("feature_id") or "").strip())
    return m.group(0).lower() if m else None


def consensus(client, drawing, sheet_png, samples=2, model=None, effort=None, max_tokens=10000):
    """Run the critic `samples` times in parallel and merge. Returns (merged output, summed call info).

    merged = {matches, issues (high issues raised by every sample, on the same feature), advisories (the rest),
    samples (raw outputs)}"""
    with ThreadPoolExecutor(samples) as pool:
        runs = list(pool.map(lambda _: critique(client, drawing, sheet_png, model, effort, max_tokens), range(samples)))
    infos = [inf for _, inf in runs]
    info = {"usage": {k: sum(inf["usage"].get(k, 0) for inf in infos) for k in ("input_tokens", "output_tokens")},
            "cost_usd": sum(inf["cost_usd"] for inf in infos), "seconds": max(inf["seconds"] for inf in infos),
            "model": infos[0].get("model"), "effort": infos[0].get("effort")}
    return merge([o for o, _ in runs]), info


def merge(outs):
    """Merge critic samples: high issues raised by every sample on the same feature block, the rest are advisories."""
    keys = [{_key(i) for i in o["issues"] if i["severity"] == "high"} for o in outs]
    agreed = set.intersection(*keys) if keys else set()
    issues, advice, seen = [], [], set()
    for i in outs[0]["issues"]:
        if i["severity"] == "high" and _key(i) in agreed and _key(i) not in seen:
            issues.append(i)
            seen.add(_key(i))
    for o in outs:
        for i in o["issues"]:
            if i not in issues and i not in advice:
                advice.append(i)
    return {"matches": not issues, "issues": issues, "advisories": advice, "samples": outs,
            "agreed_features": sorted(k for k in agreed if k)}


def blocking(critic_out):
    """Issues that trigger a correction: high severity only (wrong or missing shape, orientation).

    In the loop evaluation every medium "position" claim was a misreading of the image, while numeric sizes and
    positions are checked exactly by the callouts. Medium and low issues are still reported, as advisories.
    """
    return [i for i in critic_out["issues"] if i["severity"] == "high"]


def advisories(critic_out):
    if "advisories" in critic_out:
        return critic_out["advisories"]
    return [i for i in critic_out["issues"] if i["severity"] != "high"]
