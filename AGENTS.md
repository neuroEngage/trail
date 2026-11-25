# AGENTS.md — read this first

You are a worker on the **Data Quality Workbench**. You do NOT work from memory or guesses.

## Before any task
1. Read `context/PRODUCT.md`, `context/ARCHITECTURE.md`, `context/DATA_MODEL.md`, `context/DECISIONS.md`, `context/CODING_RULES.md`, `context/TESTING.md`.
2. Read your feature brief in `context/features/` (e.g. `F3.md`). Your brief is your scope. Anything under "Out of scope" is forbidden.
3. `context/API_CONTRACT.yaml` is the single source of truth between UI and backend. Never change it yourself — propose a change in your completion note and stop.

## Non-negotiables
- Source data is NEVER modified. Raw versions are immutable. Cleaning always creates a NEW version.
- Every state change writes an `audit_event`.
- No secrets in code, logs, or DB plaintext.
- Tests first: failing tests from the acceptance criteria, then code.
- A feature is not done until every box in `context/templates/gate_checklist.md` is ticked and `context/templates/completion_note.md` is filled in at `context/completion/Fx.md`.
- Do not touch another worker's folder. If you need a change there, write it in your completion note.

## Folder ownership
See `context/WORKERS.md`.
