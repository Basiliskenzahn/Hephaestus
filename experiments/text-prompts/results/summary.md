| prompt | kind | expected | status | result | detail | cost |
|---|---|---|---|---|---|---|
| 80×50×6 plate, four Ø6 holes 8 mm from each edge, Ø20 centre bore | complete | the true part | pass | ok |  | $0.0023 |
| A 70 x 40 x 20 mm block with R5 vertical corners and a 1 mm chamfer on | complete | the true part | pass | ok |  | $0.0009 |
| L-bracket 60 mm wide, base and upright both 40 mm, 5 mm thick, upright | complete | the true part | pass | **fail** | 40 (height) | $0.0029 |
| Flanged bushing on a vertical axis: Ø40 x 5 mm flange at the bottom, Ø | complete | the true part | pass | ok |  | $0.0017 |
| 100 x 60 x 10 mm plate with a centred 50 x 30 pocket, 4 mm deep, R3 in | complete | the true part | pass | ok |  | $0.0021 |
| Split shaft collar: a Ø40 x 15 mm ring with a Ø20 bore, a 2 mm wide sl | complete | the true part | pass | ok |  | $0.0022 |
| a small bracket for a 20 mm pipe | vague | a part + assumptions | pass | ok | 5/6 parameters marked assumed; given sizes kept: True | $0.0015 |
| a mounting plate for a NEMA 17 stepper motor | vague | a part + assumptions | pass | ok | 5/5 parameters marked assumed; given sizes kept: True | $0.0016 |
| a spacer for M5 screws, 10 mm long | vague | a part + assumptions | pass | ok | 2/3 parameters marked assumed; given sizes kept: True | $0.0013 |
| a simple washer | no_size | ask for a size | rejected | ok | A simple washer needs sizes to build. Please send its outer diameter, inner diameter (hole) and thickness. | $0.0001 |
| a 50 mm cube with a 60 mm hole through it | contradictory | clear message | rejected | ok | A 60 mm hole cannot fit in a 50 mm cube. Please send a description where the hole diameter is smaller than the cube, or increase the cube si | $0.0008 |
| a 40 x 40 x 5 plate with four Ø10 holes 2 mm from each edge | contradictory | clear message | pass | **fail** |  | $0.0009 |

10/12 as expected; cost $0.018.

Notes:
- l_bracket: "base and upright both 40 mm" is ambiguous (the model built a 40 mm upright on top of the 5 mm base, 45
  overall; the truth is 40 overall). A wording problem in the test prompt, not a bug; left as is.
- nema17: the first run rejected it for "no sizes"; the read prompt now treats a named standard (NEMA 17, M5, 608
  bearing) as a size. Re-run: 31 mm hole square, Ø22 boss, M3 clearance, every choice marked "assumed:".
- edge_holes: expected a rejection, but the model chose the buildable reading (2 mm from the hole rim to the
  edge, centres 7 mm in) and said so in the spec description. Arguably the better behaviour; counted as a miss
  against the expectation written before the run.
