"""Compatibility CLI for the canonical HomeRadar collection engine."""

from run_homeradar import (
    ACCESS_STATUSES,
    CASES,
    OUTPUT,
    ROOT,
    publish_summary,
    run_case,
    run_suite,
    build_export,
    save_report,
    smoke_main as main,
    summarize,
)

__all__ = [
    "ACCESS_STATUSES",
    "CASES",
    "OUTPUT",
    "ROOT",
    "publish_summary",
    "run_case",
    "run_suite",
    "save_report",
    "main",
    "summarize",
    "build_export",
]

if __name__ == "__main__":
    raise SystemExit(main())
