# Data Quality Workbench — Antigravity starter kit

This folder is the **context pack + worker setup** for building the Data Quality Workbench in Google Antigravity. It contains no application code. The AI workers write that.

## What is inside
| Path | Purpose |
|---|---|
| `AGENTS.md` | Read-first instructions every agent follows |
| `.agent/rules/` | Always-on workspace rules (context, tests, stop conditions) |
| `.agent/workflows/` | Slash commands: `/build-feature`, `/run-gate`, `/qa-write-tests`, `/security-review`, `/pm-signoff` |
| `context/` | PRODUCT, ARCHITECTURE, DATA_MODEL, DECISIONS, CODING_RULES, TESTING, WORKERS, BUILD_ORDER, API_CONTRACT.yaml |
| `context/features/` | F0-F8 briefs, one per worker |
| `context/templates/` | Gate checklist, completion note, brief template |
| `context/completion/` | Workers write their completion notes here |
| `prompts/` | Kickoff prompt per worker, ready to paste |
| `testdata/` | Golden clean/dirty datasets, answer key, edge files, generator |

## Setup (10 minutes)
1. Unzip, then `cd dq-workbench && git init && git add -A && git commit -m "context pack"`.
2. Open the folder in Antigravity as the workspace.
3. Open **Agent Manager**. Confirm the rules in `.agent/rules` and workflows in `.agent/workflows` are picked up (check the customizations/rules panel; if your version uses a different folder name, move them there and keep `AGENTS.md` at the root).
4. Review `context/DECISIONS.md`. Change anything you disagree with and put a date in the Date column.
5. Optional: `python3 testdata/generate_golden.py --big` to add the 1M-row file.

## Run order
Follow `context/BUILD_ORDER.md`. For each feature:
1. **QA agent:** paste `prompts/10_QA.md`, then run `/qa-write-tests` for the feature.
2. **Feature agent:** paste its prompt from `prompts/`, approve the plan, let it run `/build-feature`.
3. **Gate:** agent runs `/run-gate` and writes `context/completion/Fx.md`.
4. **Security agent** (F1, F6, F7, F8): paste `prompts/11_Security.md`, run `/security-review`.
5. **You:** run `/pm-signoff`, follow the demo script on `golden_dirty.csv`.
6. Merge, tag `v0.1-Fx`, run the full regression, then move on.

Start with `prompts/01_W0_platform.md` (F0). Do not start F1 until F0 passes its gate.

## Tips
- One agent per conversation, one feature per agent. Fresh context keeps them on-brief.
- If an agent wants to change `API_CONTRACT.yaml`, you decide, then update the file yourself.
- Never accept "tests pass" without evidence; the gate requires each box to cite a test.
- Keep MVP scope: F1 to F5 plus drop-duplicates from F6.
