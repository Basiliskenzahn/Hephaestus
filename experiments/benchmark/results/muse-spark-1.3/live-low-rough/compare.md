# Read effort low vs medium, rough pencil photos, full loop (live preset otherwise)

8 rough images, 1 run each. Medium = the Oct 27 live benchmark (`../live`, critic 1 sample, no shape check);
low = Oct 28 (`python bench.py --sets rough --runs 1 --preset live --read-effort low --tag live-low-rough
--max-iters 3`), with the shape check, 2-sample critic and the one-critic-correction rule.

| read effort | perfect, read only | perfect, after loop | read time (median) | read output tokens (median) | first part (median / max) | verified part (median) | cost per run (mean) |
|---|---|---|---|---|---|---|---|
| medium (Oct 27) | 7/8 | 7/8 | 22 s | 6.2k | 23 s / 66 s | 37 s (7 verified) | $0.063 |
| low (Oct 28) | 5/8 | 5/8 | 18 s | 3.6k | 24 s / 60 s | 37 s (6 verified) | $0.066 |

Low effort misread two parts the medium read got right, and the loop did not recover them:
- L-bracket: base thickness and slot length misread; the read is self-consistent, so every check passes (`pass`,
  not perfect).
- Enclosure: the cable hole through both walls; the critic caught it, the corrections flip-flopped (`oscillating`).
The angled bracket fails at both efforts (the 15/10 swap known from the benchmark).

Decision (rule: switch to low only if at most one fewer perfect part): **keep medium**. Low saves ~4 s of read
time on these images, not the 13 s of the Oct 27 matrix (14 vs 27 s), and costs two parts.

Caveat on "first part": the low runs ran 4 in parallel in one process, and the shape check's pure-Python SAM mask
decoding competes for the GIL: the gap between the end of the read and the compiled part was 4-13 s here, against
0.6 s in the single production run of Oct 27. Read time is the fair comparison.
