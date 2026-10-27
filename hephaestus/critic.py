"""Visual critic: compare the input drawing with a render of the compiled part. Fresh context, no generator reasoning."""
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


def critique(client, drawing, sheet_png):
    """Returns (CriticOutput as dict, call info)."""
    content = [meta_api.text("Image 1: the drawing."), meta_api.image(drawing),
               meta_api.text("Image 2: the part built from it."), meta_api.image(sheet_png, "image/png")]
    out, info = client.respond(CRITIC_PROMPT, content, json_schema(CriticOutput), "CriticOutput")
    return CriticOutput.model_validate(out).model_dump(), info


def blocking(critic_out):
    """Issues that trigger a correction: high severity only (wrong or missing shape, orientation).

    In the loop evaluation every medium "position" claim was a misreading of the image, while numeric sizes and
    positions are checked exactly by the callouts. Medium and low issues are still reported, as advisories.
    """
    return [i for i in critic_out["issues"] if i["severity"] == "high"]


def advisories(critic_out):
    return [i for i in critic_out["issues"] if i["severity"] != "high"]
