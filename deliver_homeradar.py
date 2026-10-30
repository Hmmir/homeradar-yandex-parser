"""Publish only a verified current-run manifest, without exposing source data.

The browser never receives delivery credentials. HTTPS redirects are intentionally
unsupported, and an HTTP status alone never proves that an import was applied.
"""

import hashlib
import json
import math
import re
import ssl
import time
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit

from export_homeradar import build_export
from storage import save_result


ACK_LIMIT = 65_536
RETRYABLE_STATUSES = frozenset({502, 503, 504})
STOP_STATUSES = frozenset({401, 403, 429})
ACKNOWLEDGED_STATUSES = frozenset({"applied", "unchanged"})


def _json(data):
    """Reject ambiguous objects and non-JSON numeric constants at every boundary."""

    def reject_constant(_value):
        raise ValueError("invalid_json")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    return json.loads(
        data, parse_constant=reject_constant, object_pairs_hook=unique_object
    )


def _contained_file(output, relative):
    target = (output / relative).resolve(strict=True)
    if not target.is_relative_to(output) or not target.is_file():
        raise ValueError("invalid_manifest_path")
    return target


def _preflight(output, expected_run_id):
    """Freeze validated payload bytes before the first external side effect."""
    manifest = _json(_contained_file(output, "manifest.json").read_bytes())
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or not isinstance(manifest.get("run_id"), str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", manifest["run_id"])
        is None
        or not isinstance(manifest.get("organizations"), list)
        or (expected_run_id is not None and manifest["run_id"] != expected_run_id)
    ):
        raise ValueError("invalid_manifest")
    prepared, seen = [], set()
    for entry in manifest["organizations"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid_manifest_entry")
        oid, digest, count = (
            entry.get(key) for key in ("organization_id", "sha256", "reviews_count")
        )
        if (
            not isinstance(oid, str)
            or re.fullmatch(r"[0-9]+", oid) is None
            or oid in seen
            or entry.get("file") != f"collection/{oid}/apify.json"
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
            or type(count) is not int
            or count < 1
        ):
            raise ValueError("invalid_manifest_entry")
        seen.add(oid)
        payload = _contained_file(output, entry["file"]).read_bytes()
        digest = digest.lower()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("content_hash_mismatch")
        rows = _json(payload)
        if not isinstance(rows, list) or not rows or len(rows) != count:
            raise ValueError("invalid_ready_array")
        work = f"collection/{oid}/work/{oid}"
        snapshot = _json(_contained_file(output, f"{work}/raw/{oid}.json").read_bytes())
        details = _json(
            _contained_file(output, f"{work}/public-details.json").read_bytes()
        )
        if not isinstance(snapshot, dict) or snapshot.get("organization_id") != oid:
            raise ValueError("source_organization_mismatch")
        expected = build_export(snapshot, details)
        # Canonical JSON equality also distinguishes true from 1 and 1 from 1.0.
        # The canonical exporter remains the only definition of the nine fields.
        canonical = {"sort_keys": True, "ensure_ascii": False, "allow_nan": False}
        if json.dumps(rows, **canonical) != json.dumps(expected, **canonical):
            raise ValueError("source_export_mismatch")
        prepared.append((oid, digest, count, payload))
    return manifest["run_id"], prepared


def _configuration(url, token):
    if not isinstance(url, str) or not isinstance(token, str):
        raise ValueError("invalid_configuration")
    if not url and not token:
        return None
    if not url or not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ValueError("invalid_configuration")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("invalid_configuration")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "#" in url
    ):
        raise ValueError("invalid_configuration")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid_configuration")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    # http.client sends the request target as ASCII; do not silently rewrite URLs.
    target.encode("ascii")
    return parsed.hostname, port, target


def _acknowledgment(body, oid, digest, count):
    if len(body) > ACK_LIMIT:
        return None
    try:
        value = _json(body)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("organization_id") != oid
        or not isinstance(value.get("status"), str)
        or value.get("status") not in ACKNOWLEDGED_STATUSES
        or value.get("content_sha256") != digest
        or type(value.get("reviews_count")) is not int
        or value["reviews_count"] != count
    ):
        return None
    return value["status"]


def _post(endpoint, token, run_id, prepared, timeout):
    """Return only allowlisted facts, never exception messages or response data."""
    oid, digest, count, payload = prepared
    host, port, target = endpoint
    connection = None
    try:
        kwargs = {"timeout": timeout}
        if port is not None:
            kwargs["port"] = port
        connection = HTTPSConnection(host, **kwargs)
        connection.request(
            "POST",
            target,
            body=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Homeradar-Organization-Id": oid,
                "X-Homeradar-Run-Id": run_id,
                "X-Homeradar-Content-SHA256": digest,
                "Idempotency-Key": f"{oid}:{digest}",
            },
        )
        response = connection.getresponse()
        code = response.status
        result = {"status": "failed", "reason": "http_error", "http_status": code}
        if code != 200:
            # Do not read error bodies or follow Location headers, even on HTTPS.
            return result, code in RETRYABLE_STATUSES, code in STOP_STATUSES
        acknowledgment = _acknowledgment(
            response.read(ACK_LIMIT + 1), oid, digest, count
        )
        if acknowledgment is None:
            result["reason"] = "invalid_acknowledgment"
        else:
            result.update(status=acknowledgment, reason="acknowledged")
        return result, False, False
    except ssl.SSLError:
        return {"status": "failed", "reason": "tls_error"}, False, False
    except (OSError, HTTPException):
        return {"status": "failed", "reason": "network_error"}, True, False
    except (ValueError, UnicodeError):
        return {"status": "failed", "reason": "protocol_error"}, False, False
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def _persist(output, report):
    report["acknowledged"] = sum(
        row["status"] in ACKNOWLEDGED_STATUSES for row in report["organizations"]
    )
    report["applied"] = sum(
        row["status"] == "applied" for row in report["organizations"]
    )
    report["site_updated"] = report["applied"] > 0
    save_result(output / "delivery.json", report)
    return report


def deliver_run(
    output: Path,
    url: str,
    token: str,
    expected_run_id: str | None = None,
    *,
    budget_seconds: float = 1500,
) -> dict:
    """Validate, publish and atomically checkpoint a safe delivery report.

    Acknowledgment means applied OR unchanged. ``site_updated`` specifically means
    at least one applied acknowledgment, not merely a successful HTTP request.
    A failed manifest preflight prevents every POST, including otherwise valid
    entries. Ordinary remote failures are isolated; 401/403/429 stop the queue.
    """
    output = Path(output).resolve()
    report = {
        "schema_version": 1,
        "run_id": None,
        "status": "failed",
        "reason": "invalid_manifest",
        "site_updated": False,
        "delivery_succeeded": False,
        "acknowledged": 0,
        "applied": 0,
        "organizations": [],
    }
    started = time.monotonic()
    try:
        run_id, prepared = _preflight(output, expected_run_id)
    except Exception:
        # Untrusted input exceptions can contain review text and filesystem paths.
        return _persist(output, report)
    report["run_id"] = run_id
    report["organizations"] = [
        {
            "organization_id": oid,
            "content_sha256": digest,
            "reviews_count": count,
            "status": "not_run",
            "reason": "pending",
            "attempts": [],
        }
        for oid, digest, count, _payload in prepared
    ]
    try:
        if (
            type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds)
            or budget_seconds <= 0
        ):
            raise ValueError("invalid_budget")
        endpoint = _configuration(url, token)
    except (ValueError, UnicodeError, OverflowError):
        report["reason"] = "invalid_configuration"
        for row in report["organizations"]:
            row["reason"] = "invalid_configuration"
        return _persist(output, report)
    if endpoint is None:
        report.update(status="collection_only", reason="not_configured")
        for row in report["organizations"]:
            row["reason"] = "not_configured"
        return _persist(output, report)
    if not prepared:
        report["reason"] = "no_organizations"
        return _persist(output, report)

    report.update(status="delivering", reason="in_progress")
    _persist(output, report)  # A report must be writable before any external effect.
    deadline = started + budget_seconds
    stopped, exhausted = False, False
    for row, item in zip(report["organizations"], prepared):
        if stopped or time.monotonic() >= deadline:
            exhausted = exhausted or not stopped
            row["reason"] = "delivery_stopped" if stopped else "budget_exhausted"
            _persist(output, report)
            continue
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                exhausted = True
                row.update(
                    status="failed" if row["attempts"] else "not_run",
                    reason="budget_exhausted",
                )
                _persist(output, report)
                break
            result, retryable, stopped = _post(
                endpoint, token, run_id, item, min(60, remaining)
            )
            row["attempts"].append({"attempt": attempt, **result})
            row.pop("http_status", None)
            row.update(result)
            _persist(output, report)
            if not retryable or attempt == 2:
                break
            if deadline - time.monotonic() <= 2:
                exhausted = True
                break
            time.sleep(2)

    succeeded = all(
        row["status"] in ACKNOWLEDGED_STATUSES for row in report["organizations"]
    )
    report["delivery_succeeded"] = succeeded
    if succeeded:
        report.update(status="succeeded", reason="acknowledged")
    else:
        partial = exhausted or any(
            row["status"] in ACKNOWLEDGED_STATUSES for row in report["organizations"]
        )
        report.update(
            status="partial_failure" if partial else "failed",
            reason="budget_exhausted" if exhausted else "delivery_incomplete",
        )
    return _persist(output, report)
