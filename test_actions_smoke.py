"""Check smoke-run validation and public-report privacy without external requests."""

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import actions_smoke as smoke
from storage import save_result
from test_homeradar import OID, example


def saved_export(root):
    folder = Path(root) / OID
    snapshot, details = example()
    snapshot["error"] = "PRIVATE_DIAGNOSTIC_DO_NOT_PUBLISH"
    work = folder / "work" / OID
    save_result(work / "raw" / f"{OID}.json", snapshot)
    save_result(work / "public-details.json", details)
    save_result(folder / "apify.json", smoke.build_export(snapshot, details))
    return folder


class SummaryTests(unittest.TestCase):
    def test_rejected_report_exposes_only_numeric_field_diagnostics(self):
        with TemporaryDirectory() as root:
            folder = Path(root) / OID
            snapshot, _ = example()
            snapshot["status"] = "incomplete_fields"
            snapshot["reviews"][0].update(
                date=None, text_truncated=True, full_text_confirmed=False
            )
            snapshot["text_mismatch_observations"] = {
                "total": 2,
                "whitespace_prefix": 2,
                "private": "PRIVATE",
            }
            save_result(folder / "work" / OID / "raw" / f"{OID}.json", snapshot)
            result = smoke.summarize(OID, folder, 1)
        self.assertEqual(
            result["diagnostics"],
            {
                "saved_reviews": 1,
                "missing_date": 1,
                "missing_text": 0,
                "invalid_rating": 0,
                "truncated": 1,
                "unconfirmed": 1,
                "duplicate_profiles": 0,
                "duplicate_profile_instants": 0,
                "duplicate_full_texts": 0,
                "profile_alias_merges": 0,
                "text_mismatch_total": 2,
                "text_mismatch_whitespace_prefix": 2,
            },
        )
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_diagnoses_equivalent_timestamp_identities_without_exposing_them(self):
        snapshot, _ = example()
        first = snapshot["reviews"][0]
        first["author_url"] = "https://yandex.ru/maps/user/private-profile"
        snapshot["reviews"].append(
            dict(first, id="local-2", date="2026-10-01T15:00:00+03:00")
        )
        with TemporaryDirectory() as root:
            folder = Path(root) / OID
            save_result(folder / "work" / OID / "raw" / f"{OID}.json", snapshot)
            result = smoke.summarize(OID, folder, 1)
        for key in (
            "duplicate_profiles",
            "duplicate_profile_instants",
            "duplicate_full_texts",
        ):
            self.assertEqual(result["diagnostics"][key], 1)
        self.assertNotIn("private-profile", json.dumps(result))

    def test_success_revalidates_saved_export_and_only_publishes_counts(self):
        with TemporaryDirectory() as root:
            folder = saved_export(root)
            result = smoke.summarize(OID, folder, 0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["reviews"], 1)
        self.assertEqual(result["avatars"], 1)
        self.assertEqual(result["replies"], 1)
        public = json.dumps(result, ensure_ascii=False)
        for private in (
            "Отличный дом",
            "Тестовый автор",
            "avatar.jpg",
            "Спасибо",
            "PRIVATE_DIAGNOSTIC",
        ):
            self.assertNotIn(private, public)

    def test_mismatched_payload_fails_without_including_review_text(self):
        with TemporaryDirectory() as root:
            folder = saved_export(root)
            payload = json.loads((folder / "apify.json").read_bytes())
            payload[0]["text"] = "PRIVATE_CHANGED_REVIEW"
            save_result(folder / "apify.json", payload)
            with self.assertRaises(ValueError):
                smoke.summarize(OID, folder, 0)

    def test_failed_run_cannot_publish_an_export(self):
        with TemporaryDirectory() as root:
            folder = saved_export(root)
            with self.assertRaises(ValueError):
                smoke.summarize(OID, folder, 1)

    def test_failed_collector_is_not_a_success(self):
        with TemporaryDirectory() as root:
            folder = Path(root) / OID
            save_result(
                folder / "work" / OID / "raw" / f"{OID}.json",
                {
                    "status": "incomplete_fields",
                    "reviews": [],
                    "error": "PRIVATE_ERROR",
                    "company": {"reviews_count": 1},
                },
            )
            result = smoke.summarize(OID, folder, 1)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["collector_status"], "incomplete_fields")
        self.assertNotIn("PRIVATE_ERROR", json.dumps(result))


class RunnerTests(unittest.TestCase):
    @patch("actions_smoke.subprocess.run")
    def test_source_pagination_diagnostic_excludes_non_numeric_fields(self, run):
        def rejected(*args, **kwargs):
            kwargs["stdout"].write(
                json.dumps(
                    {
                        "event": "homeradar_source_metadata",
                        "pagination": {
                            "page": 1,
                            "totalPages": 12,
                            "count": 1623,
                            "limit": 50,
                            "offset": 0,
                            "private": "PRIVATE",
                        },
                    }
                )
                + "\n"
            )
            return subprocess.CompletedProcess(args[0], 1)

        run.side_effect = rejected
        with TemporaryDirectory() as root:
            result = smoke.run_case(OID, Path(root))
        self.assertEqual(result.get("source_pagination", {}).get("totalPages"), 12)
        self.assertNotIn("PRIVATE", json.dumps(result))

    @patch("actions_smoke.publish_summary")
    @patch("actions_smoke.run_suite", return_value={"passed": 2})
    def test_cli_accepts_and_deduplicates_selected_organizations(self, run, publish):
        with patch(
            "sys.argv", ["actions_smoke.py", "--organization", OID, "987654321", OID]
        ):
            self.assertEqual(smoke.main(), 0)
        self.assertEqual(run.call_args.args[1], (OID, "987654321"))

    @patch("actions_smoke.subprocess.run")
    def test_rejection_exposes_fixed_reason_code_not_private_log(self, run):
        def rejected(*args, **kwargs):
            kwargs["stdout"].write(
                "ExportValidationError: [review_mismatch] PRIVATE review details\n"
            )
            return subprocess.CompletedProcess(args[0], 1)

        run.side_effect = rejected
        with TemporaryDirectory() as root:
            result = smoke.run_case(OID, Path(root))
        self.assertEqual(result.get("reason"), "review_mismatch")
        self.assertNotIn("PRIVATE", json.dumps(result))

    @patch("actions_smoke.publish_summary")
    @patch("actions_smoke.run_suite", return_value={"passed": 1})
    def test_cli_can_run_only_the_organization_under_investigation(self, run, publish):
        with patch("sys.argv", ["actions_smoke.py", "--organization", OID]):
            self.assertEqual(smoke.main(), 0)
        self.assertEqual(run.call_args.args[1], (OID,))

    @patch("actions_smoke.run_case")
    @patch("actions_smoke.time.sleep")
    def test_suite_uses_actual_catalog_size_not_hardcoded_ten(self, sleep, run):
        run.return_value = {"organization_id": OID, "status": "passed"}
        with TemporaryDirectory() as root:
            output = Path(root) / "run"
            result = smoke.run_suite(output, (OID,))
            markdown = (output / "summary.md").read_text(encoding="utf-8")
        self.assertEqual(result["planned"], 1)
        self.assertIn("Attempted: 1/1", markdown)
        self.assertEqual(run.call_count, 1)

    @patch("actions_smoke.subprocess.run")
    def test_child_logs_are_captured_not_streamed_to_public_console(self, run):
        run.return_value = subprocess.CompletedProcess([], 1)
        with TemporaryDirectory() as root:
            result = smoke.run_case(OID, Path(root))
            self.assertTrue((Path(root) / OID / "run.log").exists())
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.args[0][2], OID)

    @patch("actions_smoke.subprocess.run")
    def test_access_restriction_stops_remaining_companies(self, run):
        def restricted(*args, **kwargs):
            kwargs["stdout"].write("PRIVATE error: HTTP Error 429\n")
            return subprocess.CompletedProcess(args[0], 1)

        run.side_effect = restricted
        with TemporaryDirectory() as root:
            output = Path(root) / "test"
            report = smoke.run_suite(output)
            persisted = json.loads((output / "summary.json").read_bytes())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report["attempted"], 1)
        self.assertEqual(len(report["results"]), 10)
        self.assertEqual(report["results"][0]["status"], "access_restricted")
        self.assertEqual(report["results"][1]["status"], "not_run")
        self.assertNotIn("PRIVATE", json.dumps(persisted))

    def test_cannot_overwrite_a_previous_run(self):
        with TemporaryDirectory() as root:
            with self.assertRaises(FileExistsError):
                smoke.run_suite(Path(root))


if __name__ == "__main__":
    unittest.main()
