"""Part spec v1: the structured format the model fills in and the compiler turns into geometry.

Units are mm. Origin = lower-left-front corner of the part's bounding box; X right, Y back, Z up.

Every dimension is a `Num`: a number, or a string expression over parameters ("width - 8").

Sketch planes and their 2D coordinates (u, v); w is the plane normal axis:
  XY -> (u, v, w) = (x, y, z)
  XZ -> (u, v, w) = (x, z, y)
  YZ -> (u, v, w) = (y, z, x)
Holes and slots can instead name a face of the current part's bounding box
("top", "bottom", "front", "back", "left", "right"); top/bottom use XY, front/back XZ, left/right YZ.

Run `python -m hephaestus.spec` to print the JSON Schema.
"""
import json
import re
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Num = Union[float, str]
Point = Annotated[list[Num], Field(min_length=2, max_length=2, description="[u, v] in the sketch plane")]
PlaneName = Literal["XY", "XZ", "YZ"]
FaceName = Literal["top", "bottom", "front", "back", "left", "right"]
Direction = Literal["positive", "negative", "symmetric"]
Operation = Literal["add", "cut"]

EDGE_SELECTOR_RE = r"^(vertical_outer|top_outer|bottom_outer|all_outer|top_of_feature:[A-Za-z][A-Za-z0-9_\-]*)$"
FEATURE_ID_RE = r"^[A-Za-z][A-Za-z0-9_\-]*$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Parameter(_Model):
    value: Num
    description: str = ""
    min: Optional[float] = None
    max: Optional[float] = None


# ---------------------------------------------------------------- profiles (2D, in plane coordinates)
class Rect(_Model):
    shape: Literal["rect"]
    u: Num = 0
    v: Num = 0
    width: Num = Field(description="extent along u")
    height: Num = Field(description="extent along v")
    anchor: Literal["corner", "center"] = Field("corner", description="(u, v) is the lower-left corner or the centre")


class RoundedRect(_Model):
    shape: Literal["rounded_rect"]
    u: Num = 0
    v: Num = 0
    width: Num
    height: Num
    radius: Num = Field(description="corner radius, at most half the smaller side")
    anchor: Literal["corner", "center"] = "corner"


class Circle(_Model):
    shape: Literal["circle"]
    u: Num
    v: Num
    diameter: Num


class Polygon(_Model):
    shape: Literal["polygon"]
    points: list[Point] = Field(min_length=3, description="closed automatically")


class LineSeg(_Model):
    kind: Literal["line"]
    to: Point


class Arc3Seg(_Model):
    kind: Literal["arc3"]
    via: Point = Field(description="any point on the arc between its start and end")
    to: Point


class ArcCenterSeg(_Model):
    kind: Literal["arc_center"]
    center: Point
    to: Point
    clockwise: bool = Field(False, description="direction in the (u, v) plane")


Segment = Annotated[Union[LineSeg, Arc3Seg, ArcCenterSeg], Field(discriminator="kind")]


class Path(_Model):
    shape: Literal["path"]
    start: Point
    segments: list[Segment] = Field(min_length=1, description="each segment starts where the previous ended; "
                                                              "closed with a line back to start if needed")


Profile = Annotated[Union[Rect, RoundedRect, Circle, Polygon, Path], Field(discriminator="shape")]


# ---------------------------------------------------------------- features
class _Feature(_Model):
    id: str = Field(pattern=FEATURE_ID_RE)


class Extrude(_Feature):
    type: Literal["extrude"]
    plane: PlaneName
    offset: Num = Field(0, description="position of the sketch plane along its normal axis")
    profile: Profile
    distance: Num
    direction: Direction = Field("positive", description="along the plane's normal axis (+Z for XY, +Y for XZ, +X for YZ)")
    operation: Operation = "add"


class Revolve(_Feature):
    type: Literal["revolve"]
    plane: PlaneName
    offset: Num = 0
    profile: Profile
    axis: Literal["u", "v"] = Field(description="axis direction in the sketch plane")
    axis_origin: Point = Field(default_factory=lambda: [0, 0], description="a point on the axis, in (u, v)")
    angle: Num = 360
    operation: Operation = "add"


class Counterbore(_Model):
    diameter: Num
    depth: Num


class Countersink(_Model):
    diameter: Num
    angle: Num = Field(90, description="included angle in degrees")


class _Placed(_Feature):
    """Holes and slots: placed on a bounding-box face, or on an explicit plane."""
    face: Optional[FaceName] = Field(None, description="drill into this face of the part's bounding box")
    plane: Optional[PlaneName] = Field(None, description="alternative to face: start on this plane at offset")
    offset: Num = 0
    direction: Literal["positive", "negative"] = Field("negative", description="drill direction when using plane")
    center: Point
    depth: Optional[Num] = None
    through: bool = False

    @model_validator(mode="after")
    def _placement(self):
        if (self.face is None) == (self.plane is None):
            raise ValueError(f"feature {self.id}: give exactly one of 'face' or 'plane'")
        if self.through == (self.depth is not None):
            raise ValueError(f"feature {self.id}: give either 'through': true or a 'depth', not both or neither")
        return self


class Hole(_Placed):
    type: Literal["hole"]
    diameter: Num
    counterbore: Optional[Counterbore] = None
    countersink: Optional[Countersink] = None

    @model_validator(mode="after")
    def _one_head(self):
        if self.counterbore and self.countersink:
            raise ValueError(f"feature {self.id}: a hole can have a counterbore or a countersink, not both")
        return self


class Slot(_Placed):
    type: Literal["slot"]
    length: Num = Field(description="overall end-to-end length")
    width: Num
    angle: Num = Field(0, description="degrees from the u axis, counter-clockwise in (u, v)")


class LinearPattern(_Feature):
    type: Literal["linear_pattern"]
    feature: str = Field(description="id of an earlier hole or slot")
    count_u: Num = 1
    spacing_u: Num = 0
    count_v: Num = 1
    spacing_v: Num = 0


class CircularPattern(_Feature):
    type: Literal["circular_pattern"]
    feature: str = Field(description="id of an earlier hole or slot")
    center: Point
    count: Num
    angle: Num = Field(360, description="total angle; 360 spreads the copies evenly around")


class Fillet(_Feature):
    type: Literal["fillet"]
    radius: Num
    edges: str = Field(pattern=EDGE_SELECTOR_RE,
                       description="vertical_outer | top_outer | bottom_outer | all_outer | top_of_feature:<id>")


class Chamfer(_Feature):
    type: Literal["chamfer"]
    size: Num
    edges: str = Field(pattern=EDGE_SELECTOR_RE,
                       description="vertical_outer | top_outer | bottom_outer | all_outer | top_of_feature:<id>")


class Shell(_Feature):
    type: Literal["shell"]
    thickness: Num = Field(description="wall thickness, material kept inside the current outer surface")
    open_faces: list[FaceName] = Field(default_factory=list)


Feature = Annotated[Union[Extrude, Revolve, Hole, Slot, LinearPattern, CircularPattern, Fillet, Chamfer, Shell],
                    Field(discriminator="type")]


class PartSpec(_Model):
    spec_version: Literal["1"] = "1"
    name: str
    description: str = ""
    units: Literal["mm"] = "mm"
    parameters: dict[str, Parameter] = Field(default_factory=dict)
    features: list[Feature] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids(self):
        seen = set()
        for f in self.features:
            if f.id in seen:
                raise ValueError(f"duplicate feature id '{f.id}'")
            seen.add(f.id)
        for name in self.parameters:
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                raise ValueError(f"parameter name '{name}' must be an identifier (letters, digits, _)")
        return self


# ---------------------------------------------------------------- JSON Schema export
def _inline(node, defs):
    if isinstance(node, dict):
        if "$ref" in node:
            target = defs[node["$ref"].split("/")[-1]]
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return _inline({**target, **rest}, defs)
        # oneOf -> anyOf: equivalent here (members differ in a const discriminator) and more widely supported
        return {("anyOf" if k == "oneOf" else k): _inline(v, defs) for k, v in node.items()
                if k not in ("$defs", "discriminator", "title")}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def json_schema():
    """JSON Schema for PartSpec with every $ref inlined (no $defs, no recursion), for structured output."""
    raw = PartSpec.model_json_schema()
    return _inline(raw, raw.get("$defs", {}))


def schema_depth(node):
    """Nesting depth counted in object/array levels, the way structured-output limits count it."""
    if isinstance(node, dict):
        own = 1 if node.get("type") in ("object", "array") else 0
        children = [v for k, v in node.items() if isinstance(v, (dict, list))]
        if "properties" in node:
            children = [node["properties"][k] for k in node["properties"]] + \
                       [v for k, v in node.items() if k != "properties" and isinstance(v, (dict, list))]
        return own + max((schema_depth(c) for c in children), default=0)
    if isinstance(node, list):
        return max((schema_depth(c) for c in node), default=0)
    return 0


def load(path_or_dict):
    if isinstance(path_or_dict, (dict, PartSpec)):
        return path_or_dict if isinstance(path_or_dict, PartSpec) else PartSpec.model_validate(path_or_dict)
    with open(path_or_dict) as f:
        return PartSpec.model_validate(json.load(f))


if __name__ == "__main__":
    print(json.dumps(json_schema(), indent=2))
