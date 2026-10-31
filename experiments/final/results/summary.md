# Final benchmark (live preset, 1 run each)

| input | runs | perfect read | perfect after loop | perfect after one tap per flagged number | flags per run (soft / red) | false flags | first part (median / p90) | verified part (median / p90) | cost per run |
|---|---|---|---|---|---|---|---|---|---|
| printed drawing, clean | 8 | 7/8 | 7/8 | 7/8 | 0.8 / 0.0 | 6 | 29 s / 72 s | 44 s / 101 s | $0.076 |
| printed drawing, phone photo | 8 | 7/8 | 7/8 | 7/8 | 0.4 / 0.0 | 3 | 28 s / 33 s | 46 s / 75 s | $0.082 |
| printed drawing, rough pencil photo | 8 | 7/8 | 7/8 | 7/8 | 1.1 / 0.0 | 9 | 28 s / 43 s | 42 s / 93 s | $0.089 |
| hand sketch, scan | 8 | 6/8 | 7/8 | 7/8 | 0.8 / 0.0 | 6 | 28 s / 41 s | 52 s / 94 s | $0.082 |
| hand sketch, phone photo | 8 | 6/8 | 6/8 | 7/8 | 1.6 / 0.1 | 13 | 25 s / 37 s | 42 s / 60 s | $0.084 |

Per run:

| run | status | read | loop | taps | flags (soft/red; caught/false) | failed (after loop) | first part | verified | cost |
|---|---|---|---|---|---|---|---|---|---|
| 01_plate__clean | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 23.2 | 34.0 | $0.049 |
| 02_l_bracket__clean | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 131.2 | 154.6 | $0.102 |
| 03_block__clean | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 23.7 | 40.9 | $0.079 |
| 04_bushing__clean | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 26.9 | 35.6 | $0.059 |
| 05_enclosure__clean | pass | ✓ | ✓ | ✓ | 2/0; 0/2 | - | 31.3 | 42.0 | $0.070 |
| 06_angled_bracket__clean | pass | ✗ | ✗ | ✗ | 0/0; 0/0 | slope, 15, Ø8 thru, 10 | 24.7 | 47.0 | $0.075 |
| 07_pocket_plate__clean | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 72.5 | 88.4 | $0.080 |
| 08_shaft_collar__clean | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 71.7 | 100.8 | $0.096 |
| 01_plate__photo | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 18.7 | 27.6 | $0.060 |
| 02_l_bracket__photo | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 33.0 | 75.0 | $0.100 |
| 03_block__photo | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 30.1 | 45.5 | $0.088 |
| 04_bushing__photo | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 21.6 | 26.8 | $0.044 |
| 05_enclosure__photo | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 25.5 | 38.4 | $0.075 |
| 06_angled_bracket__photo | pass | ✗ | ✗ | ✗ | 1/0; 0/1 | slope, 15, Ø8 thru, 10 | 75.8 | 102.7 | $0.137 |
| 07_pocket_plate__photo | disputed | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 16.6 | - | $0.074 |
| 08_shaft_collar__photo | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 32.2 | 50.6 | $0.075 |
| 01_plate__rough | pass | ✓ | ✓ | ✓ | 4/0; 0/4 | - | 20.5 | 27.3 | $0.049 |
| 02_l_bracket__rough | unverified | ✗ | ✗ | ✗ | 1/0; 0/1 | 5 (base), slot 20 | 43.4 | 107.8 | $0.186 |
| 03_block__rough | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 22.7 | 42.5 | $0.074 |
| 04_bushing__rough | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 25.8 | 33.5 | $0.060 |
| 05_enclosure__rough | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 27.0 | 40.6 | $0.072 |
| 06_angled_bracket__rough | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 61.0 | 92.9 | $0.112 |
| 07_pocket_plate__rough | disputed | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 28.2 | - | $0.077 |
| 08_shaft_collar__rough | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 33.5 | 49.8 | $0.081 |
| 01_plate__scan | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 24.2 | 31.7 | $0.046 |
| 02_l_bracket__scan | pass | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 39.0 | 49.5 | $0.073 |
| 03_block__scan | pass | ✗ | ✗ | ✗ | 0/0; 0/0 | 2x Ø6 (12, 20), 2x Ø6 (58, 20), 46 | 29.8 | 51.7 | $0.074 |
| 04_bushing__scan | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 21.8 | 31.0 | $0.045 |
| 05_enclosure__scan | pass | ✗ | ✓ | ✓ | 1/0; 0/1 | - | 23.8 | 94.0 | $0.119 |
| 06_angled_bracket__scan | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 91.7 | 114.3 | $0.137 |
| 07_pocket_plate__scan | disputed | ✓ | ✓ | ✓ | 3/0; 0/3 | - | 25.9 | - | $0.065 |
| 08_shaft_collar__scan | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 41.1 | 85.7 | $0.101 |
| 01_plate__photo | pass | ✓ | ✓ | ✓ | 3/0; 0/3 | - | 23.4 | 32.6 | $0.057 |
| 02_l_bracket__photo | pass | ✓ | ✓ | ✓ | 3/0; 0/3 | - | 30.3 | 43.6 | $0.076 |
| 03_block__photo | pass | ✗ | ✗ | ✗ | 1/1; 0/2 | 2x Ø6 (12, 20), 2x Ø6 (58, 20), 46 | 22.9 | 40.1 | $0.079 |
| 04_bushing__photo | pass | ✗ | ✗ | ✓ | 1/0; 1/0 | 5 (flange) | 20.0 | 26.0 | $0.047 |
| 05_enclosure__photo | unverified | ✓ | ✓ | ✓ | 1/0; 0/1 | - | 27.4 | 49.0 | $0.091 |
| 06_angled_bracket__photo | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 65.3 | 122.5 | $0.162 |
| 07_pocket_plate__photo | pass | ✓ | ✓ | ✓ | 4/0; 0/4 | - | 22.2 | 36.7 | $0.075 |
| 08_shaft_collar__photo | pass | ✓ | ✓ | ✓ | 0/0; 0/0 | - | 36.8 | 59.6 | $0.081 |

Number re-read: median 4.2 s, $0.0053 per run (in parallel with the first compile and checks).

## Text prompts

| prompt | kind | status | as expected | detail | first part | cost |
|---|---|---|---|---|---|---|
| A 70 x 40 x 20 mm block with R5 vertical corners and a 1 mm  | complete | pass | yes |  | 15.6 | $0.042 |
| Flanged bushing on a vertical axis: Ø40 x 5 mm flange at the | complete | pass | yes |  | 13.6 | $0.031 |
| a 50 mm cube with a 60 mm hole through it | contradictory | pass | yes | conflict flagged on 50 mm cube, 60 mm hole; 50 mm cube with a centered through hole. The written 60 mm hole ca | 31.6 | $0.044 |
| a 40 x 40 x 5 plate with four Ø10 holes 2 mm from each edge | contradictory | pass | yes | 40x40x5 plate with four dia 10 through holes. Assumed: 2 mm means clearance from hole edge to plate edge (cent | 37.6 | $0.056 |
| L-bracket 60 mm wide, base and upright both 40 mm, 5 mm thic | complete | pass | yes |  | 30.6 | $0.073 |
| a mounting plate for a NEMA 17 stepper motor | vague | pass | yes | 5/5 parameters marked assumed; given sizes kept: True | 31.6 | $0.054 |
| a small bracket for a 20 mm pipe | vague | pass | yes | 7/8 parameters marked assumed; given sizes kept: True | 14.4 | $0.030 |
| 80×50×6 plate, four Ø6 holes 8 mm from each edge, Ø20 centre | complete | pass | yes |  | 11.8 | $0.037 |
| 100 x 60 x 10 mm plate with a centred 50 x 30 pocket, 4 mm d | complete | pass | yes |  | 20.3 | $0.049 |
| Split shaft collar: a Ø40 x 15 mm ring with a Ø20 bore, a 2  | complete | pass | yes |  | 22.4 | $0.044 |
| a spacer for M5 screws, 10 mm long | vague | pass | yes | 2/3 parameters marked assumed; given sizes kept: True | 9.4 | $0.027 |
| a simple washer | no_size | rejected | yes | This description gives no sizes for the washer. Please send its outer diameter, inner (hole) diameter and thic | - | $0.017 |

12/12 as expected.

## Injected errors (all checks on)

| error | part | caught by | fixed | corrections | status | cost |
|---|---|---|---|---|---|---|
| vertical corners chamfered instead of R5 | block | callout | yes | 1 | pass | $0.034 |
| the second side hole is missing | block | callout | yes | 1 | pass | $0.048 |
| vertical hole pair moved to the right end | angled_bracket | callout | yes | 1 | pass | $0.045 |
| slope profile mirrored (flat top at the right) | angled_bracket | shape | yes | 1 | pass | $0.062 |
| centre bore moved 5 mm along X | plate | callout | yes | 1 | pass | $0.026 |
| base slot turned 90 degrees | l_bracket | callout | yes | 1 | pass | $0.050 |
| plate 50 x 80 instead of 80 x 50 | plate | callout | yes | 1 | pass | $0.025 |
| cable hole drilled through both walls | enclosure | critic | no | 1 | unverified | $0.048 |
| upright at the front edge instead of the back | l_bracket | shape | yes | 1 | pass | $0.050 |
| bore Ø10 instead of Ø12 | block | callout | yes | 1 | pass | $0.050 |

False alarms on the 8 correct parts: 0/8

Total: 70 runs, $4.49.
