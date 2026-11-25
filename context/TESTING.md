# TESTING

## Pyramid
| Layer | What | When |
|---|---|---|
| Unit | checks, parsers, scoring maths | every commit |
| Integration | API + DB + job queue + real files | every PR |
| Golden data | planted defects vs answer key | every PR on F3-F7 |
| End to end | connect -> profile -> assess -> fix -> compare -> report | nightly + each milestone |
| Performance | large files, wide tables, concurrent jobs | milestones |
| Security | credentials, SQL injection, upload abuse, role bypass | F1, F6, F7, F8 + final |
| UAT | real analyst on their own messy dataset | before launch |

## Golden data (`/testdata`)
Generate with `python testdata/generate_golden.py` (add `--big` for the 1M-row file).
- `golden_clean.csv`: 1,000 rows, zero defects -> must score ~100.
- `golden_dirty.csv` + `golden_dirty_answer_key.json`: same data with planted defects.
- `edge/`: empty, one column, Latin-1, UTF-16, mixed types, 500 columns, optional 1M rows.

## Targets
- Detection on planted defects: recall >= 95%, precision >= 90%.
- Clean dataset overall score >= 98; dirty dataset must be visibly lower and each deduction traceable to an issue.

## Gate
See `templates/gate_checklist.md`. A feature is not done until all boxes are ticked with evidence.
