"""Manual live CLI test; public reports contain counts, never review content."""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from export_homeradar import build_export
from storage import save_result


# The same ten public HomeRadar organization links used in the local acceptance run.
CASES = (
    "41564867946",
    "19838055208",
    "193140287625",
    "135246019176",
    "88658546284",
    "207009583649",
    "243170996991",
    "111725813466",
    "1105853143",
    "244293880848",
)
ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "review_results" / "actions-smoke"
ACCESS_STATUSES = {"captcha", "access_denied", "rate_limited"}


def summarize(oid, folder, exit_code):
    work = folder / "work" / oid
    raw_path = work / "raw" / f"{oid}.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    status = raw.get("status", "no_snapshot")
    if (
        not isinstance(status, str)
        or not status.isascii()
        or not status.replace("_", "").isalpha()
    ):
        status = "unknown"
    result = {
        "organization_id": oid,
        "exit_code": exit_code,
        "collector_status": status,
        "status": "rejected",
    }
    target = folder / "apify.json"
    if exit_code:
        if target.exists():
            raise ValueError("Failed CLI run published an export")
        return result
    details = json.loads((work / "public-details.json").read_text(encoding="utf-8"))
    expected = build_export(raw, details)
    rows = json.loads(target.read_text(encoding="utf-8"))
    if rows != expected or raw.get("organization_id") != oid:
        raise ValueError("Saved export differs from verified source data")
    result.update(
        status="passed",
        reviews=len(rows),
        business_rating=rows[0]["businessRating"],
        ratings_count=rows[0]["businessRatingsCount"],
        avatars=sum(bool(row["authorAvatarUrl"]) for row in rows),
        replies=sum(bool(row["businessComment"]) for row in rows),
    )
    return result


def run_case(oid, output):
    folder = output / oid
    folder.mkdir()
    log_path = folder / "run.log"
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        try:
            child = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "export_homeradar.py"),
                    oid,
                    "--output",
                    str(folder / "apify.json"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1000,
                check=False,
                env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"),
            )
            result = summarize(oid, folder, child.returncode)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.TimeoutExpired,
        ) as error:
            traceback.print_exc(file=log)
            result = {
                "organization_id": oid,
                "status": "error",
                "error_type": type(error).__name__,
            }
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    private_log = log_path.read_text(encoding="utf-8").lower()
    if result.get("collector_status") in ACCESS_STATUSES or any(
        marker in private_log
        for marker in ("captcha", "http error 403", "http error 429")
    ):
        result["status"] = "access_restricted"
    return result


def save_report(output, report):
    report["attempted"] = sum(row["status"] != "not_run" for row in report["results"])
    report["passed"] = sum(row["status"] == "passed" for row in report["results"])
    save_result(output / "summary.json", report)
    lines = [
        "# HomeRadar manual smoke test",
        "",
        f"Attempted: {report['attempted']}/10. Passed: {report['passed']}/10.",
        "",
        "| Organization ID | Result | Collector | Reviews | Seconds |",
        "|---|---|---|---:|---:|",
    ]
    for row in report["results"]:
        lines.append(
            f"| {row['organization_id']} | {row['status']} | {row.get('collector_status', '—')} "
            f"| {row.get('reviews', '—')} | {row.get('elapsed_seconds', '—')} |"
        )
    lines += [
        "",
        "No site updates. No review texts, authors, avatars or raw logs published.",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_suite(output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "results": []}
    save_report(output, report)
    blocked = False
    for oid in CASES:
        if blocked:
            result = {"organization_id": oid, "status": "not_run"}
        else:
            if report["results"]:
                time.sleep(20)
            result = run_case(oid, output)
            blocked = result["status"] == "access_restricted"
        report["results"].append(result)
        save_report(output, report)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_report(output, report)
    return report


def main():
    report = run_suite(OUTPUT)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write((OUTPUT / "summary.md").read_text(encoding="utf-8"))
    return 0 if report["passed"] == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
