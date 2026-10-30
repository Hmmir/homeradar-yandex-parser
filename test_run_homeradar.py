"""Daily run boundaries; fictional exports, no external requests."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import run_homeradar as runner
from storage import save_result
from test_homeradar import OID, example
from export_homeradar import build_export


class DailyRunTests(unittest.TestCase):
    @patch("run_homeradar.time.sleep")
    @patch("run_homeradar.run_case")
    def test_verified_manifest_checkpoint_survives_later_collection_crash(
        self, case, sleep
    ):
        def collect(oid, output):
            if oid != OID:
                raise RuntimeError("collector crashed")
            return self.ready(oid, output)

        case.side_effect = collect
        with TemporaryDirectory() as root:
            output = Path(root) / "run"
            with self.assertRaises(RuntimeError):
                runner.collect_run(output, (OID, "987654321"), run_id="123:1")
            manifest = json.loads((output / "manifest.json").read_bytes())
        self.assertEqual(
            [row["organization_id"] for row in manifest["organizations"]], [OID]
        )

    @patch("actions_catalog.discover_catalog")
    @patch("run_homeradar.run_suite")
    @patch("run_homeradar.time.monotonic", return_value=100)
    def test_catalog_discovery_consumes_the_collection_budget(
        self, clock, suite, discover
    ):
        def registry(_output):
            clock.return_value = 200
            return {
                "status": "complete",
                "errors": [],
                "organizations": [{"organization_id": OID}],
            }

        discover.side_effect = registry
        suite.return_value = {"planned": 1, "results": []}
        with TemporaryDirectory() as root:
            runner.collect_run(Path(root) / "run", run_id="123:1")
        self.assertEqual(suite.call_args.kwargs.get("budget_seconds"), 315 * 60 - 100)

    @patch("deliver_homeradar.deliver_run")
    def test_final_summary_cannot_claim_no_updates_after_applied_ack(self, deliver):
        deliver.return_value = {
            "status": "succeeded",
            "site_updated": True,
            "acknowledged": 1,
            "applied": 1,
        }
        with TemporaryDirectory() as root:
            output = Path(root)
            (output / "collection").mkdir()
            runner.save_report(
                output / "collection",
                {
                    "planned": 1,
                    "results": [{"organization_id": OID, "status": "passed"}],
                },
            )
            (output / "summary.md").write_text(
                "No site updates. Site delivery has not run.", encoding="utf-8"
            )
            with patch("sys.argv", ["run_homeradar.py", "deliver", "--output", root]):
                self.assertEqual(runner.main(), 0)
            text = (output / "summary.md").read_text(encoding="utf-8")
        self.assertNotIn("No site updates", text)
        self.assertNotIn("Site delivery has not run", text)
        self.assertIn("Site updated: True", text)

    @patch("run_homeradar.subprocess.run")
    def test_supplementary_http_401_stops_source_queue(self, child):
        import subprocess

        def denied(*args, **kwargs):
            kwargs["stdout"].write("HTTPError: HTTP Error 401: Unauthorized\n")
            return subprocess.CompletedProcess(args[0], 1)

        child.side_effect = denied
        with TemporaryDirectory() as root:
            result = runner.run_case(OID, Path(root))
        self.assertEqual(result["status"], "access_restricted")

    def test_dry_run_preserves_failed_manifest_reason(self):
        with TemporaryDirectory() as root:
            argv = ["run_homeradar.py", "deliver", "--output", root, "--dry-run"]
            with patch("sys.argv", argv):
                self.assertEqual(runner.main(), 1)
            report = json.loads((Path(root) / "delivery.json").read_bytes())
        self.assertEqual(report["reason"], "invalid_manifest")

    @patch("run_homeradar.subprocess.run")
    def test_collection_child_never_inherits_import_credentials(self, child):
        import subprocess

        child.return_value = subprocess.CompletedProcess([], 1)
        with (
            TemporaryDirectory() as root,
            patch.dict(
                "os.environ",
                {
                    "HOMERADAR_IMPORT_TOKEN": "PRIVATE_TOKEN",
                    "HOMERADAR_IMPORT_URL": "https://example.com/import",
                },
            ),
        ):
            runner.run_case(OID, Path(root))
        self.assertFalse("HOMERADAR_IMPORT_TOKEN" in child.call_args.kwargs["env"])
        self.assertFalse("HOMERADAR_IMPORT_URL" in child.call_args.kwargs["env"])

    def ready(self, oid, output):
        snapshot, details = example()
        folder = output / oid
        save_result(folder / "work" / oid / "raw" / f"{oid}.json", snapshot)
        save_result(folder / "work" / oid / "public-details.json", details)
        save_result(folder / "apify.json", build_export(snapshot, details))
        return {"organization_id": oid, "status": "passed", "reviews": 1}

    @patch("run_homeradar.run_case")
    def test_current_run_manifest_lists_only_verified_exports(self, case):
        case.side_effect = self.ready
        with TemporaryDirectory() as root:
            output = Path(root) / "run"
            result = runner.collect_run(output, (OID,), run_id="123:1")
            manifest = json.loads((output / "manifest.json").read_bytes())
        self.assertEqual(result["passed"], 1)
        self.assertEqual(manifest["run_id"], "123:1")
        self.assertEqual(
            manifest["organizations"][0]["file"], f"collection/{OID}/apify.json"
        )
        self.assertEqual(manifest["organizations"][0]["reviews_count"], 1)
        self.assertNotIn("Отличный", json.dumps(manifest, ensure_ascii=False))

    @patch("run_homeradar.time.sleep")
    @patch("run_homeradar.run_case")
    def test_one_rejected_company_does_not_stop_another(self, case, sleep):
        case.side_effect = lambda oid, output: (
            self.ready(oid, output)
            if oid == OID
            else {"organization_id": oid, "status": "rejected"}
        )
        with TemporaryDirectory() as root:
            result = runner.collect_run(
                Path(root) / "run", ("987654321", OID), run_id="123:1"
            )
        self.assertEqual(result["passed"], 1)
        self.assertEqual(case.call_count, 2)

    @patch("run_homeradar.run_case")
    def test_invalid_ids_rejected_before_discovery_or_collection(self, case):
        with TemporaryDirectory() as root, self.assertRaises(ValueError):
            runner.collect_run(Path(root) / "run", (OID, "../private"), run_id="123:1")
        case.assert_not_called()

    @patch(
        "run_homeradar.run_case",
        return_value={"organization_id": OID, "status": "access_restricted"},
    )
    def test_access_restriction_stops_remaining_queue(self, case):
        with TemporaryDirectory() as root:
            result = runner.collect_run(
                Path(root) / "run", (OID, "987654321"), run_id="123:1"
            )
        self.assertEqual(case.call_count, 1)
        self.assertEqual(result["results"][1]["status"], "not_run")

    @patch("run_homeradar.run_case")
    def test_cannot_reuse_old_run_directory(self, case):
        with TemporaryDirectory() as root, self.assertRaises(FileExistsError):
            runner.collect_run(Path(root), (OID,), run_id="123:1")
        case.assert_not_called()


if __name__ == "__main__":
    unittest.main()
