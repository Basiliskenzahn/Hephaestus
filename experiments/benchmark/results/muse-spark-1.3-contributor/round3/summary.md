| set | runs | perfect, loop off | perfect, loop on | compiles off / on | avg corrections | cost off / on (avg) | latency off / on (median) |
|---|---|---|---|---|---|---|---|
| clean | 16 | 16/16 | 16/16 | 16 / 16 | 0.2 | $0.0021 / $0.0190 | 53 s / 138 s |
| photo | 16 | 13/16 | 15/16 | 15 / 16 | 0.2 | $0.0022 / $0.0212 | 57 s / 130 s |
| rough | 16 | 13/16 | 12/16 | 16 / 16 | 0.3 | $0.0021 / $0.0267 | 59 s / 134 s |

| part | set | off: perfect runs | on: perfect runs | corrections | still failing (loop on) |
|---|---|---|---|---|---|
| 01_plate | clean | 2/2 | 2/2 | 0, 0 | - |
| 01_plate | photo | 2/2 | 2/2 | 0, 0 | - |
| 01_plate | rough | 2/2 | 2/2 | 0, 0 | - |
| 02_l_bracket | clean | 2/2 | 2/2 | 0, 0 | - |
| 02_l_bracket | photo | 2/2 | 2/2 | 0, 0 | - |
| 02_l_bracket | rough | 2/2 | 1/2 | 2, 1 | 5 (base), slot 20 |
| 03_block | clean | 2/2 | 2/2 | 0, 0 | - |
| 03_block | photo | 2/2 | 2/2 | 0, 1 | - |
| 03_block | rough | 2/2 | 2/2 | 0, 0 | - |
| 04_bushing | clean | 2/2 | 2/2 | 0, 1 | - |
| 04_bushing | photo | 1/2 | 2/2 | 0, 1 | - |
| 04_bushing | rough | 2/2 | 2/2 | 0, 0 | - |
| 05_enclosure | clean | 2/2 | 2/2 | 1, 1 | - |
| 05_enclosure | photo | 1/2 | 2/2 | 1, 0 | - |
| 05_enclosure | rough | 1/2 | 1/2 | 0, 2 | Ø8 front wall only (rear wall closed) |
| 06_angled_bracket | clean | 2/2 | 2/2 | 0, 0 | - |
| 06_angled_bracket | photo | 1/2 | 1/2 | 0, 0 | 15, slope |
| 06_angled_bracket | rough | 0/2 | 0/2 | 0, 0 | 10, 15, slope, Ø8 thru |
| 07_pocket_plate | clean | 2/2 | 2/2 | 0, 0 | - |
| 07_pocket_plate | photo | 2/2 | 2/2 | 0, 0 | - |
| 07_pocket_plate | rough | 2/2 | 2/2 | 0, 0 | - |
| 08_shaft_collar | clean | 2/2 | 2/2 | 0, 0 | - |
| 08_shaft_collar | photo | 2/2 | 2/2 | 0, 0 | - |
| 08_shaft_collar | rough | 2/2 | 2/2 | 0, 0 | - |
