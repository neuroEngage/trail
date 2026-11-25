---
description: Run the quality gate for the current feature
---
1. Run lint, type-check, unit tests, integration tests, and the full regression suite.
2. Run golden-data tests against the testdata/ answer keys.
3. Run the datasets in testdata/edge/ and confirm nothing crashes and errors are readable.
4. Walk through every box in context/templates/gate_checklist.md. For each, cite the test or evidence. Do not tick a box without evidence.
5. Report PASS or FAIL with a table. On FAIL, list exactly what to fix.
