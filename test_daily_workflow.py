"""Offline checks for the daily workflow's trigger, trust and public-data boundaries."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"


def steps(text):
    parts = re.split(r"^      - name: (.+)\n", text, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


class DailyWorkflowTests(unittest.TestCase):
    def setUp(self):
        path = WORKFLOWS / "daily-update.yml"
        self.assertTrue(path.is_file(), "daily workflow is missing")
        self.workflow = path.read_text(encoding="utf-8")
        self.steps = steps(self.workflow)

    def test_daily_utc_schedule_and_safe_manual_defaults(self):
        triggers = self.workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn('cron: "17 0 * * *"', triggers)
        self.assertIn("  workflow_dispatch:\n", triggers)
        self.assertRegex(
            triggers, r"organization_ids:\n(?:        .*\n)*?        type: string"
        )
        self.assertRegex(
            triggers,
            r"dry_run:\n(?:        .*\n)*?        type: boolean\n        default: true",
        )
        self.assertNotRegex(
            triggers, r"(?m)^  (push|pull_request|pull_request_target|workflow_run):"
        )

    def test_all_live_workflows_share_a_non_cancelling_lock(self):
        for name in ("daily-update.yml", "manual-smoke.yml"):
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIn(
                    "concurrency:\n  group: homeradar-live\n  cancel-in-progress: false",
                    text,
                )

    def test_read_only_checkout_and_existing_pinned_toolchain(self):
        self.assertIn("permissions:\n  contents: read\n", self.workflow)
        self.assertNotRegex(self.workflow, r"(?m)^  \w[\w-]*: write$")
        self.assertIn("runs-on: ubuntu-24.04", self.workflow)
        self.assertIn('python-version: "3.12.10"', self.workflow)
        self.assertIn("persist-credentials: false", self.steps["Checkout"])
        baseline = (WORKFLOWS / "manual-smoke.yml").read_text(encoding="utf-8")
        actions = re.findall(r"uses: (actions/[\w-]+)@([^\s]+)", self.workflow)
        self.assertEqual(len(actions), 3)
        for action, sha in actions:
            with self.subTest(action=action):
                self.assertRegex(sha, r"^[a-f0-9]{40}$")
                self.assertIn(f"uses: {action}@{sha}", baseline)

    def test_offline_tests_gate_collection_without_hiding_errors(self):
        self.assertLess(
            self.workflow.index("Offline regression tests"),
            self.workflow.index("Collect current reviews"),
        )
        self.assertIn(
            "python -m unittest discover -v", self.steps["Offline regression tests"]
        )
        self.assertIn("id: install", self.steps["Install project and Chromium"])
        self.assertIn("id: tests", self.steps["Offline regression tests"])
        self.assertIn(
            "python -m playwright install --with-deps chromium",
            self.steps["Install project and Chromium"],
        )
        self.assertNotIn("continue-on-error", self.workflow)
        self.assertNotIn("|| true", self.workflow)
        self.assertNotIn("if:", self.steps["Collect current reviews"])

    def test_collection_has_no_receiver_credentials_and_parses_ids_safely(self):
        collect = self.steps["Collect current reviews"]
        self.assertIn("ORGANIZATION_IDS: ${{ inputs.organization_ids }}", collect)
        self.assertIn("read -r -a ids", collect)
        self.assertIn("[^0-9[:space:]]", collect)
        self.assertIn('--organization "${ids[@]}"', collect)
        self.assertIn(
            "python run_homeradar.py collect --output review_results/daily", collect
        )
        self.assertNotIn("HOMERADAR_IMPORT", collect)
        self.assertNotIn("--dry-run", collect)
        self.assertIn("timeout-minutes: 317", collect)
        self.assertIn("timeout-minutes: 350", self.workflow)

    def test_delivery_runs_after_partial_collection_only_on_trusted_main(self):
        deliver = self.steps["Deliver verified current exports"]
        self.assertIn("!cancelled()", deliver)
        self.assertIn("github.ref == 'refs/heads/main'", deliver)
        self.assertIn("steps.install.outcome == 'success'", deliver)
        self.assertIn("steps.tests.outcome == 'success'", deliver)
        self.assertIn(
            "steps.collect.outcome == 'success' || steps.collect.outcome == 'failure'",
            deliver,
        )
        self.assertIn("timeout-minutes: 30", deliver)
        self.assertIn(
            "python run_homeradar.py deliver --output review_results/daily", deliver
        )
        self.assertIn("args+=(--dry-run)", deliver)
        self.assertIn(
            "github.event_name == 'workflow_dispatch' && inputs.dry_run", deliver
        )

    def test_credentials_are_isolated_to_delivery_and_blank_during_dry_run(self):
        deliver = self.steps["Deliver verified current exports"]
        condition = "(github.event_name == 'schedule' || inputs.dry_run == false)"
        for key, context in (("URL", "vars"), ("TOKEN", "secrets")):
            expression = f"{context}.HOMERADAR_IMPORT_{key}"
            self.assertEqual(self.workflow.count(expression), 1)
            self.assertIn(f"{condition} && {expression} || ''", deliver)
        for name, block in self.steps.items():
            if name != "Deliver verified current exports":
                self.assertNotIn("HOMERADAR_IMPORT", block, name)
        for name, block in self.steps.items():
            if "        run:" in block:
                script = block.split("        run:", 1)[1]
                self.assertNotIn("${{", script, name)

    def test_artifact_allowlist_never_includes_reviews_or_manifest(self):
        upload = self.steps["Upload safe summaries only"]
        self.assertIn("if: ${{ always() }}", upload)
        self.assertIn("retention-days: 3", upload)
        paths = upload.split("          path: |\n", 1)[1].split(
            "          if-no-files-found:", 1
        )[0]
        self.assertEqual(
            [line.strip() for line in paths.splitlines() if line.strip()],
            [
                "review_results/daily/catalog.json",
                "review_results/daily/collection/summary.json",
                "review_results/daily/collection/summary.md",
                "review_results/daily/delivery.json",
                "review_results/daily/summary.md",
            ],
        )

    def test_job_summary_is_attempted_even_after_a_failure(self):
        summary = self.steps["Append safe job summary"]
        self.assertIn("if: ${{ always() }}", summary)
        self.assertIn('"$GITHUB_STEP_SUMMARY"', summary)
        self.assertIn("review_results/daily/summary.md", summary)
        self.assertIn("review_results/daily/collection/summary.md", summary)
        self.assertNotRegex(summary, r"manifest|apify|raw/|run\.log|\.cache")


if __name__ == "__main__":
    unittest.main()
