# Shape check eval

Thresholds: orientation margin 0.1, aspect tolerance 1.6, holes advisory.

## Correct parts (false alarms)

| part | clean | photo | rough |
|---|---|---|---|
| 01_plate | holes adv. (top 0.0) | holes adv. (top 0.0) | holes adv. (top 0.0) |
| 02_l_bracket | holes adv. (top 0.0, front 0.0, side -0.589) | holes adv. (top 0.0, front 0.0, side -0.202) | holes adv. (top 0.0, front 0.0, side -0.328) |
| 03_block | holes adv. (top 0.0, front 0.0) | holes adv. (top 0.0, front 0.0) | holes adv. (top 0.0, front 0.0) |
| 04_bushing | holes adv. (top 0.0, front 0.0) | holes adv. (top 0.0, front 0.0) | pass (top 0.0, front 0.0) |
| 05_enclosure | pass (top 0.0, front 0.0) | pass (top 0.0, front 0.0) | pass (top 0.0, front 0.0) |
| 06_angled_bracket | holes adv. (top 0.0, front -0.251) | holes adv. (top 0.0, front -0.233) | holes adv. (top 0.0, front -0.23) |
| 07_pocket_plate | holes adv. (top 0.0, front 0.0) | holes adv. (top 0.0) | holes adv. (top 0.0, front 0.0) |
| 08_shaft_collar | holes adv. (top 0.001, front 0.0) | holes adv. (top 0.002, front 0.0) | holes adv. (top 0.003, front 0.0) |

Blocking false alarms: 0/24; hole advisories on correct parts: 20/24; unavailable: 0/24.

## Injected errors (blocking issue = caught)

| injected error | part | clean | photo | rough |
|---|---|---|---|---|
| base slot turned 90 degrees | l_bracket | missed | missed | missed |
| centre bore moved 5 mm along X | plate | missed | missed | missed |
| the second side hole is missing | block | missed (holes: +3 mismatch) | missed (holes: +3 mismatch) | missed |
| bore Ø10 instead of Ø12 | block | missed | missed | missed |
| vertical corners chamfered instead of R5 | block | missed | missed | missed |
| plate 50 x 80 instead of 80 x 50 | plate | **caught** | **caught** | **caught** |
| cable hole drilled through both walls | enclosure | missed | missed | missed |
| upright at the front edge instead of the back | l_bracket | **caught** | **caught** | **caught** |
| slope profile mirrored (flat top at the right) | angled_bracket | **caught** | **caught** | **caught** |
| vertical hole pair moved to the right end | angled_bracket | missed | missed | missed |

Orientation gap (best flip minus as-is IoU): correct parts max 0.003; injected flips min 0.258. Aspect mismatch on correct parts: max 1.50x, median 1.09x.
Holes: '+n mismatch' = n more unmatched holes than the correct part on the same image (advisory only).

## Messages on injected errors

- move_hole / clean (advisory): top view: the drawing shows 4 hole(s) or closed outline(s), the part 5. In the drawing but not on the part: back-left. On the part but not in the drawing: back-left, front-left.
- swap_xy / clean: top view: the drawing's outline is 1.52 : 1 (width along X : height along Y), the part's is 1 : 1.60. Check which sizes run along X and Y.
- swap_xy / clean (advisory): top view: the drawing shows 4 hole(s) or closed outline(s), the part 5. In the drawing but not on the part: back-right, back-left, front-right. On the part but not in the drawing: back-left, back-right, front-left, front-right.
- move_hole / photo (advisory): top view: the drawing shows 5 hole(s) or closed outline(s), the part 5. In the drawing but not on the part: back-left, front-left, front-right. On the part but not in the drawing: back-left, front-left, front-right.
- swap_xy / photo: top view: the drawing's outline is 1.55 : 1 (width along X : height along Y), the part's is 1 : 1.60. Check which sizes run along X and Y.
- swap_xy / photo (advisory): top view: the drawing shows 5 hole(s) or closed outline(s), the part 5. In the drawing but not on the part: back-right, back-left, front-left, front-right. On the part but not in the drawing: back-left, back-right, front-left, front-right.
- move_hole / rough (advisory): top view: the drawing shows 4 hole(s) or closed outline(s), the part 5. In the drawing but not on the part: front-right. On the part but not in the drawing: front-left, front-right.
- swap_xy / rough: top view: the drawing's outline is 1.28 : 1 (width along X : height along Y), the part's is 1 : 1.60. Check which sizes run along X and Y.
- swap_xy / rough (advisory): top view: the drawing shows 4 hole(s) or closed outline(s), the part 5. In the drawing but not on the part: front-right. On the part but not in the drawing: front-left, front-right.
- rotate_slot / clean (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 1. On the part but not in the drawing: centre.
- rotate_slot / clean (advisory): front view: the drawing shows 1 hole(s) or closed outline(s), the part 2. In the drawing but not on the part: left. On the part but not in the drawing: top-left, top-right.
- upright_at_front / clean: side view: the drawing has its tallest section at the back (right), the part has it at the front (left); the part's outline is the drawing's mirrored along Y. Fix the positions along Y.
- upright_at_front / clean (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 1. On the part but not in the drawing: centre.
- upright_at_front / clean (advisory): front view: the drawing shows 1 hole(s) or closed outline(s), the part 2. In the drawing but not on the part: left. On the part but not in the drawing: top-left, top-right.
- rotate_slot / photo (advisory): front view: the drawing shows 2 hole(s) or closed outline(s), the part 2. In the drawing but not on the part: left, right. On the part but not in the drawing: top-left, top-right.
- upright_at_front / photo: side view: the drawing has its tallest section at the back (right), the part has it at the front (left); the part's outline is the drawing's mirrored along Y. Fix the positions along Y.
- upright_at_front / photo (advisory): front view: the drawing shows 2 hole(s) or closed outline(s), the part 2. In the drawing but not on the part: left, right. On the part but not in the drawing: top-left, top-right.
- rotate_slot / rough (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 1. On the part but not in the drawing: centre.
- rotate_slot / rough (advisory): front view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: top-left, top-right.
- upright_at_front / rough: side view: the drawing has its tallest section at the back (right), the part has it at the front (left); the part's outline is the drawing's mirrored along Y. Fix the positions along Y.
- upright_at_front / rough (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 1. On the part but not in the drawing: centre.
- upright_at_front / rough (advisory): front view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: top-left, top-right.
- drop_hole / clean (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 2. In the drawing but not on the part: centre, right. On the part but not in the drawing: centre, left.
- wrong_diameter / clean (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 3. On the part but not in the drawing: left.
- chamfer_for_fillet / clean (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 3. On the part but not in the drawing: left.
- drop_hole / photo (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 2. In the drawing but not on the part: centre, right. On the part but not in the drawing: centre, left.
- wrong_diameter / photo (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 3. On the part but not in the drawing: left.
- chamfer_for_fillet / photo (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 3. On the part but not in the drawing: left.
- wrong_diameter / rough (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 3. On the part but not in the drawing: right.
- chamfer_for_fillet / rough (advisory): top view: the drawing shows 2 hole(s) or closed outline(s), the part 3. On the part but not in the drawing: right.
- mirror_profile / clean: front view: the drawing has its tallest section at the left, the part has it at the right; the part's outline is the drawing's mirrored along X. Fix the positions along X.
- mirror_profile / clean (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: back-left, front-left.
- mirror_profile / clean (advisory): front view: the drawing shows 2 hole(s) or closed outline(s), the part 1. In the drawing but not on the part: right.
- holes_opposite / clean (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: back-right, front-right.
- holes_opposite / clean (advisory): front view: the drawing shows 2 hole(s) or closed outline(s), the part 1. In the drawing but not on the part: right.
- mirror_profile / photo: front view: the drawing has its tallest section at the left, the part has it at the right; the part's outline is the drawing's mirrored along X. Fix the positions along X.
- mirror_profile / photo (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: back-left, front-left.
- mirror_profile / photo (advisory): front view: the drawing shows 2 hole(s) or closed outline(s), the part 1. In the drawing but not on the part: right.
- holes_opposite / photo (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: back-right, front-right.
- holes_opposite / photo (advisory): front view: the drawing shows 2 hole(s) or closed outline(s), the part 1. In the drawing but not on the part: right.
- mirror_profile / rough: front view: the drawing has its tallest section at the left, the part has it at the right; the part's outline is the drawing's mirrored along X. Fix the positions along X.
- mirror_profile / rough (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: back-left, front-left.
- mirror_profile / rough (advisory): front view: the drawing shows 3 hole(s) or closed outline(s), the part 1. In the drawing but not on the part: right, right, bottom-right. On the part but not in the drawing: bottom-right.
- holes_opposite / rough (advisory): top view: the drawing shows 0 hole(s) or closed outline(s), the part 2. On the part but not in the drawing: back-right, front-right.
- holes_opposite / rough (advisory): front view: the drawing shows 3 hole(s) or closed outline(s), the part 1. In the drawing but not on the part: right, right, bottom-right. On the part but not in the drawing: bottom-right.
