# Catalog rejection repair implementation plan

> Execute inline, preserving existing logs and atomic export validation.

**Goal:** Repair reproducible parser/export defects behind the 30 rejected organizations, and distinguish unavailable source data from parser failures.

**Scope:** `collect_yandex.py`, `export_homeradar.py`, their regression tests; `actions_smoke.py` and the manual workflow only as needed for targeted live acceptance. No WordPress writes, recurring jobs, fabricated ratings, raw-review artifacts, or changes to unrelated collectors.

## Evidence and execution

- [x] Preserve the full catalog baseline (run 41048911059): 185/215 passed, 30 rejected; 4 HomeRadar redirect cycles.
- [x] Reproduce source-level failures using current public HTML and preserve raw evidence locally, outside Git.
- [ ] Add tests, then safe diagnostic reason codes and multi-organization CLI selection to reproduce failures on the same GitHub runner. Keep current singular invocation working; validate all IDs before any requests.
- [ ] Run targeted acceptance for the 30 baseline failures. Public artifacts must contain only numeric diagnostics and fixed reason codes.
- [ ] For each confirmed code defect, add a minimal failing synthetic fixture, fix the shared source of the defect, and verify that malformed/ambiguous data still fails without overwriting exports.
- [ ] Run all offline tests and a live recheck of the repaired cards plus previously passing controls.
- [ ] Document exact observed outcomes and outstanding source problems; do not claim all-catalog success if any card remains unavailable.

## Verification

Use the existing Python 3.12 virtual environment and installed Chrome for local HTML tests. Run `python -m unittest discover -v`, `ruff check` on changed files, `git diff --check`, and manual GitHub Actions acceptance. Review the final artifact against the original 215-organization manifest. Do not upload private raw snapshots or logs.
