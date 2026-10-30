"""Canonical HomeRadar run engine; no raw review data in public summaries."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from export_homeradar import SOURCE_DIAGNOSTIC_FIELDS, build_export
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
PUBLIC_COLLECTOR_STATUSES = ACCESS_STATUSES | {
    "no_snapshot",
    "running",
    "complete",
    "capped",
    "incomplete_fields",
    "count_mismatch",
    "timeout",
    "stalled",
    "sort_unavailable",
    "interrupted",
    "error",
    "browser_closed",
    "network_error",
    "server_error",
    "not_found",
    "http_error",
    "load_timeout",
    "load_error",
}


def summarize(oid, folder, exit_code):
    work = folder / "work" / oid
    raw_path = work / "raw" / f"{oid}.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    status = raw.get("status", "no_snapshot")
    if not isinstance(status, str) or status not in PUBLIC_COLLECTOR_STATUSES:
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
    canonical = {"sort_keys": True, "ensure_ascii": False, "allow_nan": False}
    if (
        json.dumps(rows, **canonical) != json.dumps(expected, **canonical)
        or raw.get("organization_id") != oid
    ):
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
                env=dict(
                    {
                        key: value
                        for key, value in os.environ.items()
                        if key not in {"HOMERADAR_IMPORT_URL", "HOMERADAR_IMPORT_TOKEN"}
                    },
                    PYTHONIOENCODING="utf-8",
                    PYTHONUNBUFFERED="1",
                ),
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
    log_contents = log_path.read_text(encoding="utf-8")
    private_log = log_contents.lower()
    rejection = re.search(r"exportvalidationerror: \[([a-z_]+)\]", private_log)
    if rejection:
        result["reason"] = rejection.group(1)
    for line in log_contents.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(event, dict)
            and event.get("event") == "homeradar_source_metadata"
        ):
            pagination = event.get("pagination", {})
            if isinstance(pagination, dict):
                result["source_pagination"] = {
                    key: value
                    for key, value in pagination.items()
                    if key in SOURCE_DIAGNOSTIC_FIELDS and type(value) is int
                }
    if result.get("collector_status") in ACCESS_STATUSES or any(
        marker in private_log
        for marker in ("captcha", "http error 401", "http error 403", "http error 429")
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
        "Collection phase only. No review texts, authors, avatars or raw logs published.",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_suite(output, cases=CASES, *, on_result=None, budget_seconds=315 * 60):
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "planned": len(cases),
        "results": [],
    }
    save_report(output, report)
    blocked = False
    started = time.monotonic()
    for oid in cases:
        if time.monotonic() - started + 1000 >= budget_seconds:
            blocked = True
            report["budget_exhausted"] = True
        if blocked:
            result = {"organization_id": oid, "status": "not_run"}
        else:
            if report["results"]:
                time.sleep(20)
            result = run_case(oid, output)
            blocked = result["status"] == "access_restricted"
        report["results"].append(result)
        save_report(output, report)
        if on_result:
            on_result(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_report(output, report)
    return report


def publish_summary(output):
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write((output / "summary.md").read_text(encoding="utf-8"))


def current_run_id():
    identifier = os.environ.get("GITHUB_RUN_ID")
    return (
        f"{identifier}:{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
        if identifier
        else str(uuid.uuid4())
    )


def write_run_summary(output, delivery=None):
    collection = output / "collection" / "summary.md"
    text = (
        collection.read_text(encoding="utf-8")
        if collection.exists()
        else "# HomeRadar\n\nNo complete collection summary.\n"
    )
    catalog = output / "catalog.json"
    if catalog.exists():
        registry = json.loads(catalog.read_bytes())
        text += f"\nCatalog: {registry['status']}. Company pages: {registry['site_pages']}. Mapping/discovery errors: {len(registry['errors'])}.\n"
    text += "\n## Site delivery\n\n"
    if delivery is None:
        text += "Not run. Site updated: False.\n"
    else:
        text += (
            f"Status: {delivery['status']}. Site updated: {delivery['site_updated']}.\n"
        )
        text += f"Acknowledged organizations: {delivery.get('acknowledged', 0)}; changed: {delivery.get('applied', 0)}.\n"
    (output / "summary.md").write_text(text, encoding="utf-8")


def collect_run(output, cases=None, *, run_id=None):
    from actions_catalog import discover_catalog

    started = time.monotonic()
    if cases is not None:
        cases = tuple(dict.fromkeys(canonical_url(value)[0] for value in cases))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "run_id": run_id or current_run_id(),
        "organizations": [],
    }
    save_result(output / "manifest.json", manifest)
    catalog = None
    if cases is None:
        catalog = discover_catalog(output)
        if catalog["status"] != "complete":
            (output / "summary.md").write_text(
                "# HomeRadar\n\nCatalog discovery failed; no site updates.\n",
                encoding="utf-8",
            )
            raise ValueError("Catalog discovery incomplete")
        cases = tuple(item["organization_id"] for item in catalog["organizations"])

    def checkpoint(row):
        if row["status"] != "passed":
            return
        oid = row["organization_id"]
        verified = summarize(oid, output / "collection" / oid, 0)
        path = output / "collection" / oid / "apify.json"
        manifest["organizations"].append(
            {
                "organization_id": oid,
                "file": path.relative_to(output).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "reviews_count": verified["reviews"],
            }
        )
        save_result(output / "manifest.json", manifest)

    report = run_suite(
        output / "collection",
        cases,
        on_result=checkpoint,
        budget_seconds=max(0, 315 * 60 - (time.monotonic() - started)),
    )
    report["catalog_errors"] = len(catalog["errors"]) if catalog else 0
    save_report(output / "collection", report)
    write_run_summary(output)
    return report


def smoke_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", nargs="+")
    args = parser.parse_args()
    cases = (
        tuple(dict.fromkeys(canonical_url(v)[0] for v in args.organization))
        if args.organization
        else CASES
    )
    report = run_suite(OUTPUT, cases)
    publish_summary(OUTPUT)
    return 0 if report["passed"] == len(cases) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("collect", "deliver"))
    parser.add_argument(
        "--output", type=Path, default=ROOT / "review_results" / "daily"
    )
    parser.add_argument("--organization", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.action == "collect":
        if args.dry_run:
            parser.error("--dry-run applies to delivery only")
        report = collect_run(args.output, args.organization)
        return (
            0
            if report["passed"] == report["planned"] and not report["catalog_errors"]
            else 1
        )
    if args.organization:
        parser.error("--organization applies to collection only")
    from deliver_homeradar import deliver_run

    report = deliver_run(
        args.output,
        "" if args.dry_run else os.environ.get("HOMERADAR_IMPORT_URL", ""),
        "" if args.dry_run else os.environ.get("HOMERADAR_IMPORT_TOKEN", ""),
        expected_run_id=current_run_id() if os.environ.get("GITHUB_RUN_ID") else None,
    )
    if args.dry_run and report["status"] == "collection_only":
        report["reason"] = "dry_run"
        save_result(args.output / "delivery.json", report)
    write_run_summary(args.output, report)
    return 0 if report["status"] in {"collection_only", "succeeded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
