# BUILD ORDER

| # | Feature | Depends on | Milestone |
|---|---|---|---|
| 0 | Platform (F0) | none | M0 Foundation (1-1.5 wk) |
| 1 | F1 Connect | 0 | M1 See your data (2-3 wk) |
| 2 | F2 Discover | 1 | M1 |
| 3 | F3 Profile | 1, 2 | M1 |
| 4 | F4 Assess | 3 | M2 Find problems (2-3 wk) |
| 5 | F5 Investigate | 4 | M2 |
| 6 | F6 Improve | 0, 4, 5 | M3 Fix and prove (2-3 wk) |
| 7 | F7 Compare | 6 | M3 |
| 8 | F8 Report | all | M4 Ship (1.5-2 wk) |

## Loop per feature
1. PM updates `features/Fx.md`.
2. QA runs `/qa-write-tests` (failing tests first).
3. Feature worker runs `/build-feature`.
4. Worker runs `/run-gate`, writes `completion/Fx.md`.
5. Security runs `/security-review` (F1, F6, F7, F8).
6. PM runs `/pm-signoff`, follows the demo script on golden_dirty.
7. Merge, tag `v0.1-Fx`, run full regression. Only then start the next feature.
