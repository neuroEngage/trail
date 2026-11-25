# WORKERS (one Antigravity agent per row)

| Worker | Owns folder | Brief | Never touches |
|---|---|---|---|
| PM (human) | /context | all | code |
| W0 Platform | backend/app/platform, docker, CI | F0.md | feature logic |
| W1 Connect | backend/app/connect, frontend/src/features/connect | F1.md | other features |
| W2 Catalogue | .../catalogue | F2.md | |
| W3 Profiler | .../profiler | F3.md | |
| W4 Quality Engine | .../quality | F4.md | |
| W5 Investigator | .../investigate | F5.md | |
| W6 Cleaner | .../cleaner | F6.md | |
| W7 Comparator | .../compare | F7.md | |
| W8 Reporter | .../reports | F8.md | |
| QA | /tests, /testdata | all | production code |
| Security | read-only | F1, F6, F7, F8 | edits |

Kickoff prompts for each are in `/prompts`.
