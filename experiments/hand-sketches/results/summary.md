| sketch | read perfect | after loop | status | corrections | failed dimensions / errors | shape check | cost |
|---|---|---|---|---|---|---|---|
| 01_plate | yes | yes | pass | 0 | - | ok | $0.0048 |
| 01_plate__photo | no | no | pass | 0 | 4x Ø6 (8, 8), 4x Ø6 (72, 8), 4x Ø6 (8, 42), 4x Ø6 (72, 42), 8 (x) | ok | $0.0076 |
| 02_l_bracket | yes | yes | pass | 0 | - | ok | $0.0055 |
| 02_l_bracket__photo | yes | yes | pass | 0 | - | ok | $0.0085 |
| 03_block | no | no | pass | 1 | 40, R5 (5, 35), R5 (65, 35) | ok | $0.0083 |
| 03_block__photo | yes | yes | disputed | 1 | - | FALSE ALARM | $0.0088 |
| 04_bushing | yes | yes | pass | 0 | - | ok | $0.0044 |
| 04_bushing__photo | no | no | pass | 0 | 5 (flange) | ok | $0.0074 |
| 05_enclosure | yes | yes | pass | 0 | - | ok | $0.0049 |
| 05_enclosure__photo | no | no | unverified | 1 | Ø8 front wall only (rear wall closed) | ok | $0.0107 |
| 06_angled_bracket | no | no | stuck | 1 | slope, 15, Ø8 thru, 10 | ok | $0.0063 |
| 06_angled_bracket__photo | yes | yes | pass | 0 | - | ok | $0.0075 |
| 07_pocket_plate | yes | yes | disputed | 1 | - | ok | $0.0055 |
| 07_pocket_plate__photo | yes | yes | stuck | 1 | - | ok | $0.0081 |
| 08_shaft_collar | yes | yes | pass | 0 | - | ok | $0.0064 |
| 08_shaft_collar__photo | yes | yes | pass | 0 | - | ok | $0.0090 |

Perfect: read 11/16, after the loop 11/16. Shape-check false alarms (blocking issue on a perfect spec): 1/16; shape check available on 16/16. Cost $0.114.

Failure types (dev preset, 1 run each):
- crossed-out number read instead of the correction: 01_plate__photo (inset 10 struck out, 8 written next to it; read 10).
- handwriting misread: 03_block (Kalam "40" read as 60), 04_bushing__photo (flange "5" read as 3). The read is
  self-consistent, so every check agrees with it.
- known shared misread: 06_angled_bracket (15/10 swap, as in the benchmark).
- critic on a not-to-scale view: 05_enclosure__photo: the critic invented a "tapering" wall (the warped front view)
  and correctly flagged the Ø8 going through both walls; the combined correction broke a callout, so the
  one-correction rule kept the read (hallucination contained, real error not fixed).
Shape check: the only blocking issue was the aspect ratio on 03_block__photo (1.47x on a correct part: the sketch
is not to scale). Aspect is advisory since; orientation gaps on perfect specs stayed <= 0.002 (threshold 0.10), so
0/16 blocking false alarms with the current code.
