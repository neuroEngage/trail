---
description: Security reviewer - review F1, F6, F7, F8
---
Read-only review. Check: credentials encrypted and never logged, read-only DB user enforced, upload validation (type, size, encoding, zip bombs), SQL sandbox (single statement, SELECT only, timeout, row limit), RBAC on every endpoint, share-link expiry and permissions, no secrets in repo. Output findings as Critical / High / Medium / Low with file and line. Do not edit code.
