| injected error | part | detected by (per run) | fixed | corrections | status | cost / run | time / run |
|---|---|---|---|---|---|---|---|
| base slot turned 90 degrees | l_bracket | critic, critic | 2/2 | 1, 1 | pass, pass | $0.0028 | 69 s |
| centre bore moved 5 mm along X | plate | none, none | 0/2 | 0, 0 | pass, pass | $0.0006 | 26 s |
| the second side hole is missing | block | both, both | 2/2 | 3, 3 | pass, max_iters | $0.0094 | 327 s |
| bore Ø10 instead of Ø12 | block | both, both | 2/2 | 1, 1 | pass, pass | $0.0032 | 107 s |
| vertical corners chamfered instead of R5 | block | both, both | 2/2 | 2, 1 | pass, pass | $0.0042 | 152 s |
| plate 50 x 80 instead of 80 x 50 | plate | callout, callout | 2/2 | 1, 1 | pass, pass | $0.0019 | 41 s |

False alarms on unmodified true specs: 1/6 checks flagged (1 medium/low critic advisories, not blocking).
- l_bracket run 2: base_slot: slot is centered front-to-back in the model, drawing dimensions it 17.5 mm from the edge (medium)
