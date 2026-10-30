"""Publisher contract tests with fictional exports and an in-memory HTTPS peer."""

import copy
import hashlib
import io
import json
import ssl
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import deliver_homeradar as delivery
import export_homeradar
from storage import save_result
from test_homeradar import example

URL = "https://receiver.example/import?tenant=private"
TOKEN = "fictional-secret-token"
RUN_ID = "12345:1"
OID = "123456789"
OTHER_OID = "987654321"


class Response:
    def __init__(self, status=200, value=None, raw=None):
        self.status = status
        self.body = raw if raw is not None else json.dumps(value).encode("utf-8")

    def read(self, limit):
        return self.body[:limit]


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "run"
        self.manifest = {"schema_version": 1, "run_id": RUN_ID, "organizations": []}
        self.add_organization(OID)
        self.write_manifest()
        self.transport = patch("deliver_homeradar.HTTPSConnection")
        self.connection_class = self.transport.start()
        self.addCleanup(self.transport.stop)
        self.connection = self.connection_class.return_value
        self.connection.getresponse.side_effect = [self.ack()]
        sleeper = patch("deliver_homeradar.time.sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def add_organization(self, oid):
        snapshot, details = example()
        snapshot["organization_id"] = oid
        details["organization_id"] = oid
        for row in details["reviews"]:
            row["businessId"] = oid
        folder = self.output / "collection" / oid
        work = folder / "work" / oid
        save_result(work / "raw" / f"{oid}.json", snapshot)
        save_result(work / "public-details.json", details)
        target = folder / "apify.json"
        save_result(target, export_homeradar.build_export(snapshot, details))
        self.manifest["organizations"].append(
            {
                "organization_id": oid,
                "file": f"collection/{oid}/apify.json",
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "reviews_count": 1,
            }
        )

    def write_manifest(self):
        save_result(self.output / "manifest.json", self.manifest)

    def ack(self, oid=OID, status="applied", **changes):
        entry = next(
            row
            for row in self.manifest["organizations"]
            if row["organization_id"] == oid
        )
        value = {
            "organization_id": oid,
            "status": status,
            "content_sha256": entry["sha256"],
            "reviews_count": entry["reviews_count"],
        }
        value.update(changes)
        return Response(value=value)

    def run_delivery(self, **kwargs):
        return delivery.deliver_run(
            self.output,
            kwargs.pop("url", URL),
            kwargs.pop("token", TOKEN),
            expected_run_id=kwargs.pop("expected_run_id", RUN_ID),
            **kwargs,
        )

    def assert_no_request(self, report, reason=None):
        self.connection.request.assert_not_called()
        self.assertFalse(report["site_updated"])
        self.assertFalse(report["delivery_succeeded"])
        if reason is not None:
            self.assertEqual(report["reason"], reason)

    def test_posts_exact_ready_bytes_with_verified_acknowledgment(self):
        report = self.run_delivery()
        self.assertEqual(report["status"], "succeeded")
        self.assertTrue(report["site_updated"])
        self.assertTrue(report["delivery_succeeded"])
        self.assertEqual(report["acknowledged"], 1)
        self.assertEqual(report["applied"], 1)
        self.assertEqual(report["organizations"][0]["status"], "applied")
        self.connection_class.assert_called_once_with("receiver.example", timeout=60)
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("POST", "/import?tenant=private"))
        ready = self.output / self.manifest["organizations"][0]["file"]
        self.assertEqual(kwargs["body"], ready.read_bytes())
        headers = kwargs["headers"]
        digest = self.manifest["organizations"][0]["sha256"]
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["X-Homeradar-Organization-Id"], OID)
        self.assertEqual(headers["X-Homeradar-Run-Id"], RUN_ID)
        self.assertEqual(headers["X-Homeradar-Content-SHA256"], digest)
        self.assertEqual(headers["Idempotency-Key"], f"{OID}:{digest}")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(
            json.loads((self.output / "delivery.json").read_text()), report
        )
        self.connection.close.assert_called_once()

    def test_unchanged_is_acknowledged_but_not_reported_as_site_change(self):
        self.connection.getresponse.side_effect = [self.ack(status="unchanged")]
        report = self.run_delivery()
        self.assertEqual(report["status"], "succeeded")
        self.assertTrue(report["delivery_succeeded"])
        self.assertFalse(report["site_updated"])
        self.assertEqual(report["acknowledged"], 1)
        self.assertEqual(report["applied"], 0)

    def test_unconfigured_delivery_is_collection_only_without_network(self):
        report = self.run_delivery(url="", token="")
        self.assertEqual(report["status"], "collection_only")
        self.assert_no_request(report, "not_configured")
        self.assertTrue((self.output / "delivery.json").is_file())

    def test_partial_configuration_is_failure_without_network(self):
        for url, token in ((URL, ""), ("", TOKEN)):
            with self.subTest(url=bool(url)):
                report = self.run_delivery(url=url, token=token)
                self.assertEqual(report["status"], "failed")
                self.assert_no_request(report, "invalid_configuration")

    def test_url_policy_rejects_http_userinfo_fragment_and_header_injection(self):
        for url in (
            "http://receiver.example/import",
            "https:///import",
            "https://user:password@receiver.example/import",
            "https://receiver.example/import#private",
            "https://receiver.example/#",
            "https://receiver.example:invalid/import",
            "https://receiver.example/\r\nX: bad",
        ):
            with self.subTest(url=url):
                self.assert_no_request(
                    self.run_delivery(url=url), "invalid_configuration"
                )
        self.assert_no_request(
            self.run_delivery(token="secret\r\nX-Header: injected"),
            "invalid_configuration",
        )

    def test_manifest_schema_duplicate_ids_and_stale_run_never_send(self):
        original = copy.deepcopy(self.manifest)
        cases = [
            {"schema_version": True},
            {"schema_version": 2},
            {"run_id": ""},
            {"run_id": "bad\r\nInjected: header"},
            {"run_id": "previous-run"},
            {"organizations": {}},
            {"organizations": original["organizations"] * 2},
            {
                "organizations": [
                    {**original["organizations"][0], "organization_id": "../1"}
                ]
            },
            {
                "organizations": [
                    {**original["organizations"][0], "reviews_count": True}
                ]
            },
            {
                "organizations": [
                    {**original["organizations"][0], "sha256": "not-a-hash"}
                ]
            },
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.manifest = {**original, **changes}
                self.write_manifest()
                self.assert_no_request(self.run_delivery(), "invalid_manifest")

    def test_invalid_manifest_stays_failure_when_delivery_not_configured(self):
        self.manifest["run_id"] = "previous-run"
        self.write_manifest()
        self.assert_no_request(self.run_delivery(url="", token=""), "invalid_manifest")

    def test_manifest_path_must_be_exact_and_contained(self):
        for path in (
            "../outside.json",
            str(self.output / "outside.json"),
            f"collection/{OID}/../{OID}/apify.json",
            f"collection\\{OID}\\apify.json",
            f"collection/{OTHER_OID}/apify.json",
        ):
            with self.subTest(path=path):
                self.manifest["organizations"][0]["file"] = path
                self.write_manifest()
                self.assert_no_request(self.run_delivery(), "invalid_manifest")

    def test_symlink_escape_in_ready_or_source_is_rejected(self):
        target = self.output / self.manifest["organizations"][0]["file"]
        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"OS cannot create test symlink: {type(error).__name__}")
        self.assert_no_request(self.run_delivery(), "invalid_manifest")

    def test_hash_tamper_fails_preflight_before_any_organization_is_sent(self):
        self.add_organization(OTHER_OID)
        self.write_manifest()
        path = self.output / self.manifest["organizations"][1]["file"]
        path.write_bytes(path.read_bytes() + b" ")
        self.assert_no_request(self.run_delivery(), "invalid_manifest")

    def test_ready_count_fields_and_source_are_revalidated_not_only_hash(self):
        target = self.output / self.manifest["organizations"][0]["file"]
        valid = json.loads(target.read_text(encoding="utf-8"))
        bad_rows = [
            [],
            {},
            [{**valid[0], "text": "forged private review"}],
            [{**valid[0], "extra": "not part of contract"}],
            [{**valid[0], "rating": True}],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                save_result(target, rows)
                self.manifest["organizations"][0]["sha256"] = hashlib.sha256(
                    target.read_bytes()
                ).hexdigest()
                self.write_manifest()
                self.assert_no_request(self.run_delivery(), "invalid_manifest")

    def test_source_with_wrong_organization_or_incomplete_snapshot_cannot_send(self):
        path = self.output / "collection" / OID / "work" / OID / "raw" / f"{OID}.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        for changes in ({"organization_id": OTHER_OID}, {"status": "stalled"}):
            with self.subTest(changes=changes):
                save_result(path, {**snapshot, **changes})
                self.assert_no_request(self.run_delivery(), "invalid_manifest")

    def test_resolved_manifest_ready_and_source_paths_cannot_escape_output(self):
        original_resolve = Path.resolve
        outside = Path(self.temp.name) / "outside.json"
        for relative in (
            "manifest.json",
            f"collection/{OID}/apify.json",
            f"collection/{OID}/work/{OID}/raw/{OID}.json",
            f"collection/{OID}/work/{OID}/public-details.json",
        ):
            with self.subTest(relative=relative):
                target = self.output / relative
                outside.write_bytes(target.read_bytes())

                def resolved(path, strict=False):
                    return (
                        outside
                        if path == target
                        else original_resolve(path, strict=strict)
                    )

                with patch.object(Path, "resolve", resolved):
                    self.assert_no_request(self.run_delivery(), "invalid_manifest")

    def test_only_current_manifest_entries_are_sent_not_unlisted_exports(self):
        self.add_organization(OTHER_OID)
        self.manifest["organizations"].pop()
        self.write_manifest()
        report = self.run_delivery()
        self.assertEqual(report["acknowledged"], 1)
        self.connection.request.assert_called_once()

    def test_http_200_without_exact_json_ack_is_not_success(self):
        for response in (
            Response(raw=b"OK"),
            Response(value={}),
            Response(value=[]),
            self.ack(organization_id=OTHER_OID),
            self.ack(status="queued"),
            self.ack(content_sha256="0" * 64),
            self.ack(reviews_count=True),
            self.ack(reviews_count=2),
            self.ack(reviews_count=1.0),
            Response(raw=b'{"status":"applied","status":"unchanged"}'),
            Response(raw=b"x" * 65_537),
        ):
            with self.subTest(response=response.body[:40]):
                self.connection.reset_mock()
                self.connection.getresponse.side_effect = [response]
                report = self.run_delivery()
                self.assertFalse(report["site_updated"])
                self.assertEqual(
                    report["organizations"][0]["reason"], "invalid_acknowledgment"
                )
                self.connection.request.assert_called_once()

    def test_acknowledgment_with_unhashable_status_cannot_raise_or_succeed(self):
        for status in ([], {}):
            with self.subTest(status=status):
                self.connection.getresponse.side_effect = [self.ack(status=status)]
                try:
                    report = self.run_delivery()
                except Exception as error:
                    self.fail(f"Malformed JSON ACK raised {type(error).__name__}")
                self.assertEqual(
                    report["organizations"][0]["reason"], "invalid_acknowledgment"
                )
                self.assertFalse(report["site_updated"])

    def test_timeout_after_apply_retries_with_same_payload_and_idempotency_key(self):
        self.connection.getresponse.side_effect = [
            TimeoutError(f"private {URL} {TOKEN}"),
            self.ack(status="unchanged"),
        ]

        def check_report_before_retry(_seconds):
            snapshot = json.loads((self.output / "delivery.json").read_text())
            self.assertEqual(len(snapshot["organizations"][0]["attempts"]), 1)
            self.assertEqual(
                snapshot["organizations"][0]["attempts"][0]["reason"], "network_error"
            )

        self.sleep.side_effect = check_report_before_retry
        report = self.run_delivery()
        self.assertTrue(report["delivery_succeeded"])
        calls = self.connection.request.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.sleep.assert_called_once_with(2)
        self.assertEqual(len(report["organizations"][0]["attempts"]), 2)

    def test_retryable_status_codes_retry_exactly_once(self):
        for code in (502, 503, 504):
            with self.subTest(code=code):
                self.connection.reset_mock()
                self.sleep.reset_mock()
                self.connection.getresponse.side_effect = [Response(code), self.ack()]
                self.assertTrue(self.run_delivery()["delivery_succeeded"])
                self.assertEqual(self.connection.request.call_count, 2)
                self.sleep.assert_called_once_with(2)

    def test_second_network_failure_stops_retry_and_closes_connections(self):
        self.connection.getresponse.side_effect = [ConnectionResetError("private")] * 2
        report = self.run_delivery()
        self.assertFalse(report["delivery_succeeded"])
        self.assertEqual(self.connection.request.call_count, 2)
        self.assertEqual(self.connection.close.call_count, 2)
        self.sleep.assert_called_once_with(2)

    def test_latest_network_failure_does_not_retain_previous_http_status(self):
        self.connection.getresponse.side_effect = [
            Response(503),
            TimeoutError("private"),
        ]
        report = self.run_delivery()
        row = report["organizations"][0]
        self.assertEqual(row["reason"], "network_error")
        self.assertNotIn("http_status", row)
        self.assertEqual(row["attempts"][0]["http_status"], 503)
        self.assertNotIn("http_status", row["attempts"][1])

    def test_tls_certificate_failure_is_not_retried(self):
        self.connection.getresponse.side_effect = [
            ssl.SSLCertVerificationError("private")
        ]
        report = self.run_delivery()
        self.connection.request.assert_called_once()
        self.assertEqual(report["organizations"][0]["reason"], "tls_error")
        self.sleep.assert_not_called()

    def test_invalid_budget_is_safe_configuration_failure(self):
        for budget in (True, 0, -1, float("nan"), float("inf"), 10**1000):
            with self.subTest(budget=str(budget)[:20]):
                try:
                    report = self.run_delivery(budget_seconds=budget)
                except Exception as error:
                    self.fail(f"Invalid budget raised {type(error).__name__}")
                self.assert_no_request(report, "invalid_configuration")

    def test_auth_or_rate_limit_stops_remaining_deliveries(self):
        self.add_organization(OTHER_OID)
        self.write_manifest()
        for code in (401, 403, 429):
            with self.subTest(code=code):
                self.connection.reset_mock()
                self.connection.getresponse.side_effect = [
                    Response(code, raw=b"secret body")
                ]
                report = self.run_delivery()
                self.connection.request.assert_called_once()
                self.assertEqual(report["organizations"][1]["status"], "not_run")
                self.assertEqual(
                    report["organizations"][1]["reason"], "delivery_stopped"
                )
                self.assertFalse(report["delivery_succeeded"])

    def test_ordinary_failure_does_not_block_another_organization(self):
        self.add_organization(OTHER_OID)
        self.write_manifest()
        self.connection.getresponse.side_effect = [Response(400), self.ack(OTHER_OID)]
        report = self.run_delivery()
        self.assertEqual(self.connection.request.call_count, 2)
        self.assertEqual(report["status"], "partial_failure")
        self.assertTrue(report["site_updated"])
        self.assertFalse(report["delivery_succeeded"])
        self.assertEqual(report["acknowledged"], 1)

    def test_redirects_and_202_are_rejected_without_retry_or_followup(self):
        for code in (202, 301, 302, 303, 307, 308, 404, 500):
            with self.subTest(code=code):
                self.connection.reset_mock()
                self.connection.getresponse.side_effect = [Response(code)]
                report = self.run_delivery()
                self.connection.request.assert_called_once()
                self.assertFalse(report["site_updated"])
                self.assertEqual(
                    report["organizations"][0]["attempts"][0]["http_status"], code
                )
        self.sleep.assert_not_called()

    def test_budget_exhaustion_marks_remaining_not_run(self):
        self.add_organization(OTHER_OID)
        self.write_manifest()
        with patch("deliver_homeradar.time.monotonic", side_effect=[0, 0, 0, 11]):
            report = self.run_delivery(budget_seconds=10)
        self.assertEqual(report["status"], "partial_failure")
        self.assertEqual(report["organizations"][-1]["status"], "not_run")
        self.assertEqual(report["organizations"][-1]["reason"], "budget_exhausted")
        self.assertLessEqual(self.connection.request.call_count, 1)

    def test_budget_exhausted_during_retry_keeps_first_attempt_as_failed(self):
        self.connection.getresponse.side_effect = [TimeoutError("private")]
        with patch("deliver_homeradar.time.monotonic", side_effect=[0, 0, 0, 0, 11]):
            report = self.run_delivery(budget_seconds=10)
        self.connection.request.assert_called_once()
        self.assertEqual(report["organizations"][0]["status"], "failed")
        self.assertEqual(report["organizations"][0]["reason"], "budget_exhausted")
        self.assertEqual(len(report["organizations"][0]["attempts"]), 1)
        self.assertEqual(report["status"], "partial_failure")

    def test_reports_and_logs_never_expose_url_token_response_or_review(self):
        secret = "raw-private-server-response"
        self.connection.getresponse.side_effect = [
            TimeoutError(f"{URL} {TOKEN} {secret}"),
            Response(400, raw=secret.encode()),
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            report = self.run_delivery()
        public = json.dumps(report, ensure_ascii=False)
        public += (self.output / "delivery.json").read_text(encoding="utf-8")
        public += stdout.getvalue() + stderr.getvalue()
        for sensitive in (
            URL,
            TOKEN,
            secret,
            "Отличный дом!",
            "Тестовый автор",
            "avatar.jpg",
        ):
            self.assertNotIn(sensitive, public)
        self.assertEqual(list(self.output.glob("*response*")), [])


if __name__ == "__main__":
    unittest.main()
