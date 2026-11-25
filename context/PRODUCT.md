# PRODUCT

**Data Quality Workbench** takes a dataset from raw to trusted: connect it, understand it, find what is wrong, fix it only with permission, prove what changed, and hand over a report.

- **Users:** analysts and data stewards (daily use). **Buyers:** data and compliance leaders.
- **Promise:** raw data is never touched; every change is previewed, approved, logged and reversible.

## Six-stage journey
1 Connect -> 2 Discover -> 3 Profile -> 4 Assess -> 5 Investigate & Improve -> 6 Compare & Report

## Features
| ID | Name | One line |
|---|---|---|
| F1 | Connect data | Files (CSV, Excel, Parquet, JSON) and PostgreSQL; credentials encrypted; source never modified |
| F2 | Discover & preview | Catalogue view: schemas, tables, columns, rows, metadata |
| F3 | Profile data | Background profiling job: types, nulls, distinct, min/max/mean/percentiles, distributions, semantic detection |
| F4 | Quality assessment | Built-in + custom checks, score per dimension, ranked issues board |
| F5 | Investigate | Evidence per issue: flagged cells, "why flagged", mark valid/ignore, export rows |
| F6 | Improve (with permission) | Recommend -> configure -> preview -> approve -> apply as new version -> undo |
| F7 | Compare | Two versions, metrics or saved SQL, side by side with deltas |
| F8 | Report | PDF / HTML / Excel / share link, reproducible and audit-ready |

## Roles
- **Analyst:** connect, profile, assess, investigate, propose fixes, compare, report.
- **Approver:** everything an analyst can do, plus approve and apply fixes (F6).
- **Admin:** manage users, sources, retention.

## MVP
F1 (files + PostgreSQL) + F2 + F3 + F4 + F5 + a minimal F6 (one fix type, e.g. drop duplicates).
