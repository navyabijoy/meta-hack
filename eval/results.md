# Repro eval results

Run on 2026-11-05 00:46. **4 of 4 cases correct.**

- Reproduced and fixed with the hidden reference test passing: 3 of 3
- Median time, video to verified fix: 139 s
- Median Meta API cost per run: $0.117

| Case | Expected | Repro's result | Reference test | Replay attempts | Fix rounds | Time | Cost | Correct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| double-save | fixed | fixed | pass | 2 | 1 | 190 s | $0.18 | yes |
| wrong-delete | fixed | fixed | pass | 1 | 1 | 139 s | $0.12 | yes |
| counter | fixed | fixed | pass | 1 | 1 | 123 s | $0.12 | yes |
| not-a-bug-reload | not-reproduced | not-reproduced | — | 3 | — | 185 s | $0.08 | yes |

## History

- First full run (00:43): 3 of 4. wrong-delete failed: the planner named the delete button "x" while the page shows "×", so the replay could not find it. Fixed by matching icon glyphs and falling back to the row description; wrong-delete re-run at 00:46 and passed.

## Cases

- **double-save**: The video shows a click; the user actually double-clicked. Needs a hypothesis.
- **wrong-delete**: Two identical x buttons; SAM must pick the right row.
- **counter**: Stale derived state.
- **not-a-bug-reload**: The user says a task vanishes on reload; the video shows it does not. Repro must not open a PR.
