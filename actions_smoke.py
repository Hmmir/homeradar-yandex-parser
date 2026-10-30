"""Manual live CLI test; public reports contain counts, never review content."""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from export_homeradar import build_export
from collect_yandex import canonical_url, review_date
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
    rows = raw.get("reviews", [])
    result["diagnostics"] = {
        "saved_reviews": len(rows),
        "missing_date": sum(
            review_date(row) == datetime.min.replace(tzinfo=timezone.utc)
            for row in rows
        ),
        "missing_text": sum(not row.get("text") for row in rows),
        "invalid_rating": sum(
            type(row.get("rating")) is not int or row["rating"] not in range(1, 6)
            for row in rows
        ),
        "truncated": sum(row.get("text_truncated") is not False for row in rows),
        "unconfirmed": sum(row.get("full_text_confirmed") is not True for row in rows),
    }
    profiles = [row for row in rows if row.get("author_url")]
    for label, keys in (
        ("duplicate_profiles", [row["author_url"] for row in profiles]),
        (
            "duplicate_profile_instants",
            [(row["author_url"], review_date(row)) for row in profiles],
        ),
        (
            "duplicate_full_texts",
            [" ".join((row.get("text") or "").split()) for row in rows],
        ),
    ):
        result["diagnostics"][label] = len(keys) - len(set(keys))
    for key in ("total", "whitespace_prefix"):
        value = raw.get("text_mismatch_observations", {}).get(key, 0)
        result["diagnostics"][f"text_mismatch_{key}"] = (
            value if type(value) is int and value >= 0 else 0
        )
    aliases = raw.get("profile_alias_merges", 0)
    result["diagnostics"]["profile_alias_merges"] = (
        aliases if type(aliases) is int and aliases >= 0 else 0
    )
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
        f"Attempted: {report['attempted']}/{report['planned']}. Passed: {report['passed']}/{report['planned']}.",
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


def run_suite(output, cases=CASES):
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "planned": len(cases),
        "results": [],
    }
    save_report(output, report)
    blocked = False
    for oid in cases:
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


def publish_summary(output):
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write((output / "summary.md").read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization", help="Only test this Yandex organization ID or URL"
    )
    args = parser.parse_args()
    cases = (canonical_url(args.organization)[0],) if args.organization else CASES
    report = run_suite(OUTPUT, cases)
    publish_summary(OUTPUT)
    return 0 if report["passed"] == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
