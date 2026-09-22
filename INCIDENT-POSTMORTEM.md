# INCIDENT_POSTMORTEM — [template + first worked example]

## Format (director-facing: what happened, impact, cause, fix, prevention)

**Title:** NYC 311 ingest failure — disk exhaustion on the scale-tier build
**Date:** 2026-09-21 · **Duration:** 3 failed attempts over ~70 minutes · **Severity:** build-time only (no user-facing impact; demo already shipped)
**Status:** Resolved + prevention in place

### What happened
Three consecutive ingest failures of the 22.5M-row NYC 311 dataset with two distinct
error signatures: `database or disk is full` (×2) and `database is locked` (×1).

### Impact
Build time lost: ~70 min. Data integrity: zero loss — the failure mode was fail-closed
(uncommitted transactions rolled back; partial tables dropped on restart). No wrong
numbers ever shipped: the verify-then-delete rule kept the raw file until the final
ingest passed its row-count check.

### Root causes (two, both mine)
1. **WAL ballooning:** one giant transaction per table → the write-ahead log grew to
   12 GB (uncommitted pages can't checkpoint). Disk hit 991 MB free.
2. **Journal-mode conflict:** switching to DELETE journal mode to fix #1 blocked
   concurrent readers — my own verification queries then locked out the ingest writer.

### Fix (permanent, in the pipeline now)
- Per-chunk COMMIT (50k rows) + `wal_autocheckpoint=200` + `busy_timeout=60000`.
- Rule: no ad-hoc DB queries while an ingest runs (monitoring reads only).
- Disk-guard: raw files delete only after row-count verification against the official total.

### What it taught (the honest part)
These are the classic "data plumbing" failures the scoping research warns solo builders
about — 30–40% of effort, underestimated 2–3×. Two more banked today: bulk-export
endpoints that don't honor HTTP range resume (restart clean, don't resume), and CSVs
with embedded newlines (line-splitting corrupts rows; parse with a real CSV reader).
All four are now encoded in ingest_big_data.py, not in anyone's memory.

### Follow-ups
- [x] ingest_big_data.py hardened (chunk commits, busy_timeout, autocheckpoint)
- [x] verify-then-delete rule documented in DATA-MANAGEMENT.md
- [x] this postmortem as the worked example for the template