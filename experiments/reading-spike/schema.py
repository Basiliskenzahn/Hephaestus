"""Part spec v0 for the reading spike.

Deliberately simple: a part is a union of axis-aligned boxes plus holes, slots, fillets and chamfers.
That covers plates, brackets and blocks, which is enough to test whether the model reads sketches correctly.
The real Talos spec (profiles, extrudes, named parameters) comes later.

Coordinates in mm. Origin = lower-left-front corner of the part's bounding box.
X to the right, Y to the back, Z up.
Hole/slot centres are given in the plane perpendicular to the axis:
  axis Z -> (u, v) = (x, y);  axis Y -> (u, v) = (x, z);  axis X -> (u, v) = (y, z)
"""

NUM = {"type": "number"}
AXIS = {"type": "string", "enum": ["X", "Y", "Z"]}
EDGES = {"type": "string", "enum": ["vertical_outer", "top_outer", "bottom_outer", "all_outer"]}

PART_SPEC = {
    "type": "object",
    "properties": {
        "part_name": {"type": "string"},
        "views_identified": {"type": "array", "items": {"type": "string"},
                             "description": "Views found in the drawing, e.g. TOP, FRONT, SIDE"},
        "overall": {"type": "object",
                    "properties": {"x": NUM, "y": NUM, "z": NUM},
                    "required": ["x", "y", "z"]},
        "bodies": {"type": "array", "description": "Union of axis-aligned boxes forming the solid",
                   "items": {"type": "object",
                             "properties": {"name": {"type": "string"},
                                            "x0": NUM, "y0": NUM, "z0": NUM,
                                            "dx": NUM, "dy": NUM, "dz": NUM},
                             "required": ["name", "x0", "y0", "z0", "dx", "dy", "dz"]}},
        "holes": {"type": "array",
                  "items": {"type": "object",
                            "properties": {"axis": AXIS, "u": NUM, "v": NUM,
                                           "diameter": NUM, "through": {"type": "boolean"}},
                            "required": ["axis", "u", "v", "diameter", "through"]}},
        "slots": {"type": "array",
                  "items": {"type": "object",
                            "properties": {"axis": AXIS, "u": NUM, "v": NUM,
                                           "length": NUM, "width": NUM,
                                           "long_direction": AXIS},
                            "required": ["axis", "u", "v", "length", "width", "long_direction"]}},
        "fillets": {"type": "array",
                    "items": {"type": "object",
                              "properties": {"edges": EDGES, "radius": NUM},
                              "required": ["edges", "radius"]}},
        "chamfers": {"type": "array",
                     "items": {"type": "object",
                               "properties": {"edges": EDGES, "size": NUM},
                               "required": ["edges", "size"]}},
        "uncertainties": {"type": "string",
                          "description": "Anything ambiguous or inferred (e.g. positions assumed symmetric)"},
    },
    "required": ["part_name", "views_identified", "overall", "bodies", "holes",
                 "slots", "fillets", "chamfers", "uncertainties"],
}

SYSTEM_PROMPT = """You convert dimensioned engineering sketches into a structured part spec.

Conventions (follow exactly):
- All values in mm.
- Origin = lower-left-front corner of the part's bounding box. X right, Y back, Z up.
- TOP view shows X (right) and Y (up = back). FRONT view shows X (right) and Z (up). SIDE view shows Y (right = back) and Z (up).
- Model the solid as a union of axis-aligned boxes in "bodies" (one box for a plate, two for an L-bracket).
- Hole and slot centres are in the plane perpendicular to their axis: axis Z -> (u,v)=(x,y); axis Y -> (u,v)=(x,z); axis X -> (u,v)=(y,z).
- Slot "length" is the overall end-to-end length, "width" is the slot width.
- A slot's "long_direction" is the axis along which its length dimension is measured, not the direction it appears to run in the drawing.
- A note like "t = 6" is the plate thickness.
- Positions not dimensioned explicitly are usually symmetric about the part centre; if you assume that, say so in "uncertainties".
- Use only dimensions that are written on the drawing or follow directly from them. Do not guess unstated features.
Return only the JSON spec."""
