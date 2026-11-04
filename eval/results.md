# Repro eval results

Run on 2026-11-05 00:43. **3 of 4 cases correct.**

- Reproduced and fixed with the hidden reference test passing: 2 of 3
- Median time, video to verified fix: 157 s
- Median Meta API cost per run: $0.1

| Case | Expected | Repro's result | Reference test | Replay attempts | Fix rounds | Time | Cost | Correct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| double-save | fixed | fixed | pass | 2 | 1 | 190 s | $0.18 | yes |
| wrong-delete | fixed | not-reproduced | — | 3 | — | 141 s | $0.08 | no |
| counter | fixed | fixed | pass | 1 | 1 | 123 s | $0.12 | yes |
| not-a-bug-reload | not-reproduced | not-reproduced | — | 3 | — | 185 s | $0.08 | yes |

## Cases

- **double-save**: The video shows a click; the user actually double-clicked. Needs a hypothesis.
- **wrong-delete**: Two identical x buttons; SAM must pick the right row.
- **counter**: Stale derived state.
- **not-a-bug-reload**: The user says a task vanishes on reload; the video shows it does not. Repro must not open a PR.
