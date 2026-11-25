---
description: Build one feature end to end (tests first, then code, then gate)
---
Ask which feature (F0-F8). Then:
1. Read AGENTS.md and every file listed there, then context/features/<Fx>.md.
2. Write a short implementation plan as an artifact listing the files you will create. Wait for PM approval.
3. Write failing tests from the acceptance criteria (unit, integration, golden data where listed).
4. Implement until all tests pass.
5. Run /run-gate.
6. Write context/completion/<Fx>.md using context/templates/completion_note.md.
7. Stop. Do not start the next feature.
