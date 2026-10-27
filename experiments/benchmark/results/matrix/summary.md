## Read

| read model | effort | perfect | compiles | median s | p90 s | median out tok | cost / read |
|---|---|---|---|---|---|---|---|
| muse-spark-1.3 | low | 13/16 (photo 7/8, rough 6/8) | 16/16 | 14 | 23 | 3627 | $0.0273 |
| muse-spark-1.3 | medium | 14/16 (photo 7/8, rough 7/8) | 16/16 | 27 | 54 | 6604 | $0.0418 |
| muse-spark-1.3-contributor | low | 13/16 (photo 7/8, rough 6/8) | 16/16 | 31 | 84 | 3782 | $0.0016 |
| muse-spark-1.3-contributor | medium | 13/16 (photo 7/8, rough 6/8) | 15/16 | 60 | 131 | 6763 | $0.0021 |

## Critic

| critic model | effort | samples | flags injected errors | loop detects (callout or critic) | false alarms (correct parts) | median s | p90 s | median out tok / sample | cost / check |
|---|---|---|---|---|---|---|---|---|---|
| muse-spark-1.3 | low | 1 | 10/16 | 14/16 | 0/16 | 9 | 16 | 1926 | $0.0122 |
| muse-spark-1.3 | low | 2 | 8/16 | 14/16 | 0/16 | 11 | 17 | 1876 | $0.0219 |
| muse-spark-1.3 | medium | 1 | 8/16 | 14/16 | 0/16 | 15 | 20 | 3156 | $0.0166 | (1 failed calls)
| muse-spark-1.3 | medium | 2 | 6/16 | 14/16 | 0/16 | 19 | 28 | 3350 | $0.0328 | (2 failed calls)
| muse-spark-1.3-contributor | low | 1 | 8/16 | 14/16 | 1/16 | 21 | 42 | 1938 | $0.0007 |
| muse-spark-1.3-contributor | low | 2 | 7/16 | 14/16 | 1/16 | 22 | 53 | 1819 | $0.0013 |
| muse-spark-1.3-contributor | medium | 1 | 8/16 | 14/16 | 0/16 | 33 | 62 | 3023 | $0.0010 | (1 failed calls)
| muse-spark-1.3-contributor | medium | 2 | 6/16 | 14/16 | 0/16 | 43 | 72 | 3126 | $0.0017 | (3 failed calls)

Critic flags per injected error (C = flagged, - = not), copies 1 and 2:

| config | rotate_slot | move_hole | drop_hole | wrong_diameter | chamfer_for_fillet | swap_xy | through_rear_wall | upright_at_front |
|---|---|---|---|---|---|---|---|---|
| 1.3 low x1 | CC | -- | CC | CC | CC | -- | CC | -- |
| 1.3 low x2 | CC | -- | CC | -- | CC | -- | CC | -- |
| 1.3 medium x1 | CC | -- | CC | -- | CC | -- | CC | -- |
| 1.3 medium x2 | CC | -- | -C | -- | C- | -- | CC | -- |
| 1.3-contributor low x1 | CC | -- | CC | -- | CC | -- | CC | -- |
| 1.3-contributor low x2 | CC | -- | CC | -- | C- | -- | CC | -- |
| 1.3-contributor medium x1 | CC | -- | CC | -- | CC | -- | CC | -- |
| 1.3-contributor medium x2 | CC | -- | -C | -- | -C | -- | CC | -- |
