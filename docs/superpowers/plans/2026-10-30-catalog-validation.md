# Catalog rejection repair implementation plan

> Execute inline, preserving existing logs and atomic export validation.

**Goal:** Repair reproducible parser/export defects behind the 30 rejected organizations, and distinguish unavailable source data from parser failures.

**Scope:** `collect_yandex.py`, `export_homeradar.py`, their regression tests; `actions_smoke.py` and the manual workflow only as needed for targeted live acceptance. No WordPress writes, recurring jobs, fabricated ratings, raw-review artifacts, or changes to unrelated collectors.

## Evidence and execution

- [x] Preserve the full catalog baseline (run 41048911059): 185/215 passed, 30 rejected; 4 HomeRadar redirect cycles.
- [x] Reproduce source-level failures using current public HTML and preserve raw evidence locally, outside Git.
- [x] Add tests, then safe diagnostic reason codes and multi-organization CLI selection to reproduce failures on the same GitHub runner. Keep current singular invocation working; validate all IDs before any requests.
- [x] Run targeted acceptance for the 30 baseline failures. Public artifacts contain only numeric diagnostics and fixed reason codes.
- [x] Replace the two-pass HTML/DOM HomeRadar export with the already available complete public HTML source. Preserve the browser collector, its logs, legacy snapshot replay, nine-field contract, and atomic validation. Check every page rather than impose a twelve-page limit.
- [x] Run all offline tests and a live recheck of the repaired cards plus previously passing controls.
- [x] Document exact observed outcomes and outstanding source problems; do not claim all-catalog success if any card remains unavailable.

## Observed acceptance

Final code `a658a2e`, [run 41078682799](https://github.com/Hmmir/homeradar-yandex-parser/actions/runs/41078682799): all 149 offline tests passed; all 33 selected organizations attempted. Ten of the previous 30 failures and all three controls passed, producing 732 reviews. Domain-level failures keep the workflow red rather than publish partial exports.

Remaining source failures: 14 organizations without a valid overall rating; 4 with missing mandatory review fields; one with conflicting source counts (1623 vs 1624); one whose expected organization was not returned. Four HomeRadar redirect cycles remain site-side issues. The full 215-organization catalog was not rerun with this version. No site writes, REG.RU deployment, WordPress import, or recurring schedule were performed.

## Verification

Use the existing Python 3.12 virtual environment and installed Chrome for local HTML tests. Run `python -m unittest discover -v`, `ruff check` on changed files, `git diff --check`, and manual GitHub Actions acceptance. Review the final artifact against the original 215-organization manifest. Do not upload private raw snapshots or logs.
