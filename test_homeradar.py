"""HomeRadar contract checks with fictional reviews and no external requests."""

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import export_homeradar as export
from collect_yandex import collect
from test_reviews import example_html, fake_page


OID = "123456789"
DATE = "2026-10-01T12:00:00Z"
FIELDS = {
    "businessRating",
    "businessRatingsCount",
    "text",
    "rating",
    "date",
    "authorName",
    "authorAvatarUrl",
    "businessComment",
    "businessCommentDate",
}


def example():
    review = {
        "reviewId": "source-1",
        "businessId": OID,
        "text": "Отличный дом!",
        "rating": 5,
        "updatedTime": DATE,
        "author": {
            "name": "Тестовый автор",
            "avatarUrl": "https://example.com/avatar.jpg",
        },
        "businessComment": {"text": "Спасибо!", "updatedTime": "2026-10-02T12:00:00Z"},
    }
    snapshot = {
        "schema_version": 1,
        "organization_id": OID,
        "status": "complete",
        "company": {"rating": 4.7, "ratings_count": 499, "reviews_count": 1},
        "reviews": [
            {
                "id": "local-1",
                "source_review_id": "source-1",
                "author": "Тестовый автор",
                "text": "Отличный дом!",
                "rating": 5,
                "date": DATE,
                "text_truncated": False,
                "full_text_confirmed": True,
                "observation": "dom",
            }
        ],
    }
    details = {
        "organization_id": OID,
        "pages": [
            {
                "rating": {
                    "ratingValue": 4.699999809265137,
                    "ratingCount": 499,
                    "reviewCount": 1,
                },
                "pagination": {"page": 1, "totalPages": 1, "count": 1},
            }
        ],
        "reviews": [review],
    }
    return snapshot, details


def business(details, page=1, total=1):
    return {
        "id": OID,
        "ratingData": details["pages"][0]["rating"],
        "reviewResults": {
            "params": {
                "page": page,
                "totalPages": total,
                "count": len(details["reviews"]),
                "limit": len(details["reviews"]),
                "offset": (page - 1) * len(details["reviews"]),
            },
            "reviews": details["reviews"],
        },
    }


class ExportTests(unittest.TestCase):
    def test_missing_business_rating_has_stable_diagnostic_code(self):
        with self.assertRaises(ValueError) as caught:
            export.metrics({"ratingValue": 0, "ratingCount": 4, "reviewCount": 1})
        self.assertTrue(
            str(caught.exception).startswith("[business_rating_unavailable]")
        )

    def test_exact_apify_contract_including_real_supplement_fields(self):
        rows = export.build_export(*example())
        self.assertEqual(set(rows[0]), FIELDS)
        self.assertEqual(
            rows,
            [
                {
                    "businessRating": 4.7,
                    "businessRatingsCount": 499,
                    "text": "Отличный дом!",
                    "rating": 5,
                    "date": DATE,
                    "authorName": "Тестовый автор",
                    "authorAvatarUrl": "https://example.com/avatar.jpg",
                    "businessComment": "Спасибо!",
                    "businessCommentDate": "2026-10-02T12:00:00Z",
                }
            ],
        )

    def test_matches_without_source_id_and_normalizes_whitespace_and_dates(self):
        snapshot, details = example()
        snapshot["reviews"][0].pop("source_review_id")
        snapshot["reviews"][0].update(
            text="Отличный\u00a0 дом!", date="2026-10-01T15:00:00+03:00"
        )
        self.assertEqual(export.build_export(snapshot, details)[0]["date"], DATE)

    def test_anonymous_author_and_absent_optional_fields(self):
        snapshot, details = example()
        snapshot["reviews"][0]["author"] = "Анонимный отзыв"
        details["reviews"][0].update(author={}, businessComment=None)
        row = export.build_export(snapshot, details)[0]
        self.assertEqual(row["authorName"], "Анонимный отзыв")
        for key in ("authorAvatarUrl", "businessComment", "businessCommentDate"):
            self.assertEqual(row[key], "")

    def test_rejects_incomplete_snapshot_or_unconfirmed_text(self):
        for change in ({"status": "stalled"}, {"status": "capped"}):
            snapshot, details = example()
            snapshot.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                export.build_export(snapshot, details)
        for change in (
            {"text_truncated": True},
            {"full_text_confirmed": False},
            {"text": "  "},
        ):
            snapshot, details = example()
            snapshot["reviews"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                export.build_export(snapshot, details)

    def test_rejects_changed_business_metrics(self):
        for key, value in (
            ("ratingCount", 500),
            ("ratingValue", 4.8),
            ("reviewCount", 2),
        ):
            snapshot, details = example()
            details["pages"][0]["rating"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                export.build_export(snapshot, details)

    def test_rejects_missing_or_inconsistent_review_data(self):
        changes = [
            {"businessId": "999"},
            {"reviewId": "unknown"},
            {"text": "Другой текст"},
            {"rating": True},
            {"updatedTime": "2026-10-03T12:00:00Z"},
            {"updatedTime": "2026-10-01"},
            {"author": {"name": "Другой автор"}},
            {"author": {"name": "Тестовый автор", "avatarUrl": "javascript:alert(1)"}},
            {"businessComment": {"text": "Ответ без даты"}},
        ]
        for change in changes:
            snapshot, details = example()
            details["reviews"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                export.build_export(snapshot, details)

    def test_rejects_duplicate_source_ids(self):
        snapshot, details = example()
        details["reviews"].append(copy.deepcopy(details["reviews"][0]))
        with self.assertRaises(ValueError):
            export.build_export(snapshot, details)

    def test_rejects_ambiguous_content_match(self):
        snapshot, details = example()
        snapshot["reviews"][0].pop("source_review_id")
        second = copy.deepcopy(details["reviews"][0])
        second["reviewId"] = "source-2"
        details["reviews"].append(second)
        details["pages"][0]["rating"]["reviewCount"] = 2
        details["pages"][0]["pagination"]["count"] = 2
        snapshot["company"]["reviews_count"] = 2
        second = copy.deepcopy(snapshot["reviews"][0])
        second.update(id="local-2", author="Другой автор")
        snapshot["reviews"].append(second)
        with self.assertRaises(ValueError):
            export.build_export(snapshot, details)

    def test_rejects_empty_array_that_cannot_carry_business_metrics(self):
        snapshot, details = example()
        snapshot["reviews"] = []
        snapshot["company"]["reviews_count"] = 0
        details["reviews"] = []
        details["pages"][0]["rating"]["reviewCount"] = 0
        details["pages"][0]["pagination"]["count"] = 0
        with self.assertRaises(ValueError):
            export.build_export(snapshot, details)


class SourceTests(unittest.TestCase):
    @patch("export_homeradar.time.sleep")
    @patch("export_homeradar.urlopen")
    def test_network_retry_is_bounded_and_validation_is_not_retried(
        self, open_url, sleep
    ):
        from urllib.error import HTTPError

        for error in (
            TimeoutError("read timed out"),
            HTTPError("https://yandex.ru/", 503, "Unavailable", {}, None),
        ):
            open_url.reset_mock()
            open_url.side_effect = error
            with (
                self.subTest(error=type(error).__name__),
                self.assertRaises(ValueError) as caught,
            ):
                export.fetch_business(OID, 1)
            self.assertIn("[source_network_error]", str(caught.exception))
            self.assertEqual(open_url.call_count, 2)
        open_url.reset_mock()
        open_url.side_effect = ValueError("invalid source")
        with self.assertRaises(ValueError):
            export.fetch_business(OID, 1)
        self.assertEqual(open_url.call_count, 1)

    @patch("export_homeradar.time.sleep")
    @patch("export_homeradar.urlopen")
    def test_transient_read_timeout_retries_only_the_failed_page(self, open_url, sleep):
        from unittest.mock import MagicMock

        _, details = example()
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = "https://yandex.ru/maps/org/123456789/reviews/"
        response.read.return_value = (
            '<script class="state-view" type="application/json">'
            + json.dumps({"stack": [{"results": {"items": [business(details)]}}]})
            + "</script>"
        ).encode()
        open_url.side_effect = [TimeoutError("read timed out"), response]
        self.assertEqual(export.fetch_business(OID, 2)["id"], OID)
        self.assertEqual(open_url.call_count, 2)
        self.assertTrue(
            all("page=2" in call.args[0].full_url for call in open_url.call_args_list)
        )
        sleep.assert_called_once_with(2)

    @patch("export_homeradar.time.sleep")
    @patch("export_homeradar.urlopen")
    def test_http_access_restriction_is_never_retried(self, open_url, sleep):
        from urllib.error import HTTPError

        for code in (401, 403, 429):
            open_url.reset_mock()
            open_url.side_effect = HTTPError(
                "https://yandex.ru/", code, "Restricted", {}, None
            )
            with self.subTest(code=code), self.assertRaises(HTTPError):
                export.fetch_business(OID, 1)
            self.assertEqual(open_url.call_count, 1)
        sleep.assert_not_called()

    def test_public_snapshot_keeps_source_ids_and_newest_first_order(self):
        _, details = example()
        older = copy.deepcopy(details["reviews"][0])
        older.update(reviewId="source-older", updatedTime="2026-09-01T12:00:00Z")
        details["reviews"].insert(0, older)
        details["pages"][0]["rating"]["reviewCount"] = 2
        details["pages"][0]["pagination"]["count"] = 2
        snapshot = export.snapshot_from_details(details)
        self.assertEqual(
            [row["source_review_id"] for row in snapshot["reviews"]],
            ["source-1", "source-older"],
        )

    @patch("export_homeradar.time.sleep")
    @patch("export_homeradar.fetch_business")
    def test_rejects_repeated_page_offset(self, fetch, sleep):
        _, details = example()
        first, second = business(details, 1, 2), business(copy.deepcopy(details), 2, 2)
        for page in (first, second):
            page["ratingData"]["reviewCount"] = 2
            page["reviewResults"]["params"]["count"] = 2
        second["reviewResults"]["params"]["offset"] = 0
        fetch.side_effect = [first, second]
        with self.assertRaises(ValueError):
            export.collect_details(OID)

    @patch("export_homeradar.fetch_business")
    def test_incomplete_source_page_is_not_published(self, fetch):
        _, details = example()
        page = business(details)
        page["reviewResults"]["reviews"] = []
        fetch.return_value = page
        with self.assertRaises(ValueError):
            export.collect_details(OID)

    @patch("export_homeradar.fetch_business")
    def test_access_restriction_is_not_retried(self, fetch):
        from urllib.error import HTTPError

        fetch.side_effect = HTTPError("https://yandex.ru/", 429, "Limited", {}, None)
        with self.assertRaises(HTTPError):
            export.collect_details(OID)
        self.assertEqual(fetch.call_count, 1)

    def test_reads_original_state_view_without_losing_html_characters(self):
        _, details = example()
        details["reviews"][0]["text"] = "Дом <3 &amp; уют"
        state = {"stack": [{"results": {"items": [business(details)]}}]}
        html = (
            '<script class="state-view" type="application/json">'
            + json.dumps(state)
            + "</script>"
        )
        self.assertEqual(
            export.read_business(html, OID)["reviewResults"]["reviews"][0]["text"],
            "Дом <3 &amp; уют",
        )

    def test_missing_state_or_wrong_organization_is_not_empty_success(self):
        for html in (
            "<html>captcha</html>",
            '<script class="state-view" type="application/json">{}</script>',
        ):
            with self.subTest(html=html), self.assertRaises(ValueError):
                export.read_business(html, OID)

    @patch("export_homeradar.time.sleep")
    @patch("export_homeradar.fetch_business")
    def test_visits_all_declared_pages(self, fetch, sleep):
        _, details = example()
        first, second = business(details, 1, 2), business(copy.deepcopy(details), 2, 2)
        first["ratingData"]["reviewCount"] = 2
        second["ratingData"]["reviewCount"] = 2
        first["reviewResults"]["params"]["count"] = 2
        second["reviewResults"]["params"]["count"] = 2
        second["reviewResults"]["reviews"][0]["reviewId"] = "source-2"
        fetch.side_effect = [first, second]
        result = export.collect_details(OID)
        self.assertEqual(len(result["reviews"]), 2)
        self.assertEqual(fetch.call_args_list[1].args, (OID, 2))
        sleep.assert_called_once_with(1)

    @patch("export_homeradar.time.sleep")
    @patch("export_homeradar.fetch_business")
    def test_visits_all_pages_above_legacy_twelve_page_limit(self, fetch, sleep):
        _, details = example()
        pages = []
        for number in range(1, 14):
            page = business(copy.deepcopy(details), number, 13)
            page["ratingData"]["reviewCount"] = 13
            page["reviewResults"]["params"]["count"] = 13
            page["reviewResults"]["reviews"][0]["reviewId"] = f"source-{number}"
            pages.append(page)
        fetch.side_effect = pages
        self.assertEqual(len(export.collect_details(OID)["reviews"]), 13)
        self.assertEqual(fetch.call_count, 13)

    @patch("export_homeradar.fetch_business")
    def test_refuses_inconsistent_page_count_without_more_requests(self, fetch):
        _, details = example()
        fetch.return_value = business(details, total=13)
        with self.assertRaises(ValueError):
            export.collect_details(OID)
        self.assertEqual(fetch.call_count, 1)

    def test_rejects_missing_pages_or_changed_metrics_between_pages(self):
        for changed in (False, True):
            _, details = example()
            details["pages"][0]["pagination"]["totalPages"] = 2
            if changed:
                second = copy.deepcopy(details["pages"][0])
                second["pagination"]["page"] = 2
                second["rating"]["ratingCount"] += 1
                details["pages"].append(second)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                export.validate_details(details, OID)

    def test_malformed_page_is_validation_error(self):
        _, details = example()
        details["pages"] = [None]
        with self.assertRaises(ValueError):
            export.validate_details(details, OID)


class FullCollectionTests(unittest.TestCase):
    def test_yandex_profile_url_aliases_merge_html_and_dom_without_duplicates(self):
        for profile in (
            "https://yandex.com/maps/user/author-1",
            "https://yandex.ru/maps/user/author-1/",
            "https://yandex.ru/maps/user/author-1?tab=reviews",
        ):
            with self.subTest(profile=profile), TemporaryDirectory() as folder:
                _, details = example()
                details["reviews"][0]["author"]["publicId"] = "author-1"
                state = {"stack": [{"results": {"items": [business(details)]}}]}
                html = (
                    '<script class="state-view" type="application/json">'
                    + json.dumps(state)
                    + "</script>"
                )
                current = {
                    "author": "Тестовый автор",
                    "author_url": profile,
                    "date": DATE,
                    "text": "Отличный…",
                    "rating": 5,
                    "text_truncated": True,
                }
                page = fake_page(html, {"company": {}, "reviews": [current]})
                result = collect(
                    page,
                    OID,
                    export.canonical_url(OID)[1],
                    Path(folder) / "raw.json",
                    None,
                    0,
                )
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(result["reviews"]), 1)
                self.assertEqual(result["confirmed_full_texts"], 1)
                self.assertEqual(result["reviews"][0]["source_review_id"], "source-1")
                self.assertEqual(result["profile_alias_merges"], 1)

    def test_untrusted_profile_url_is_not_equivalent_to_yandex(self):
        from collect_yandex import review_key

        _, details = example()
        row = {
            "author_url": "https://yandex.ru/maps/user/author-1",
            "date": DATE,
            "text": "Тест",
        }
        trusted = review_key(OID, row)
        for profile in (
            "https://evil.example/maps/user/author-1",
            "https://yandex.ru.evil/maps/user/author-1",
            "http://yandex.ru/maps/user/author-1",
            "https://[invalid-profile",
        ):
            self.assertNotEqual(trusted, review_key(OID, dict(row, author_url=profile)))

    def test_text_mismatch_diagnostic_does_not_publish_text(self):
        from test_limits import row
        from test_reviews import URL
        from storage import save_result

        previous = dict(row(1), text="PRIVATE first\nsecond full text")
        current = dict(row(1), text="PRIVATE first second…", text_truncated=True)
        page = fake_page(example_html(0, 1), {"company": {}, "reviews": [current]})
        with TemporaryDirectory() as folder:
            target = Path(folder) / "raw.json"
            save_result(
                target, {"organization_id": OID, "reviews": [previous], "company": {}}
            )
            result = collect(page, OID, URL, target, None, 0)
        self.assertEqual(
            result["text_mismatch_observations"], {"total": 1, "whitespace_prefix": 1}
        )

    def test_html_crlf_and_dom_lf_do_not_invalidate_confirmed_full_text(self):
        state = {
            "stack": [
                {
                    "results": {
                        "items": [
                            {
                                "id": OID,
                                "title": "Тестовая компания",
                                "ratingData": {"ratingValue": 4.5, "reviewCount": 1},
                                "reviewResults": {
                                    "reviews": [
                                        {
                                            "reviewId": "source-1",
                                            "businessId": OID,
                                            "author": {
                                                "name": "Тестовый автор",
                                                "publicId": "author-1",
                                            },
                                            "updatedTime": DATE,
                                            "rating": 5,
                                            "text": "Первая строка.\r\nВторая строка.\r\nПродолжение.",
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            ],
        }
        html = (
            '<script class="state-view" type="application/json">'
            + json.dumps(state)
            + "</script>"
        )
        current = {
            "author": "Тестовый автор",
            "author_url": "https://yandex.ru/maps/user/author-1",
            "date": DATE,
            "rating": 5,
            "text_truncated": True,
            "text": "Первая строка.\nВторая строка.\nПродолжение.",
        }
        page = fake_page(html, {"company": {}, "reviews": [current]})
        with TemporaryDirectory() as folder:
            result = collect(
                page,
                OID,
                export.canonical_url(OID)[1],
                Path(folder) / "raw.json",
                None,
                0,
            )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["confirmed_full_texts"], 1)
        self.assertEqual(result["truncated_count"], 0)
        self.assertEqual(result["reviews"][0]["text"], current["text"])

    def test_full_export_does_not_require_newest_sorting(self):
        rows = [
            {
                "author": str(i),
                "author_url": None,
                "date": DATE,
                "text": f"Тестовый отзыв {i}",
                "rating": 5,
                "text_truncated": False,
            }
            for i in range(51)
        ]
        page = fake_page(
            example_html(0, 51), {"company": {"reviews_count": 51}, "reviews": rows}
        )
        with (
            TemporaryDirectory() as folder,
            patch(
                "collect_yandex.select_newest",
                side_effect=RuntimeError("sorting is unnecessary"),
            ) as sorter,
        ):
            result = collect(
                page,
                OID,
                export.canonical_url(OID)[1],
                Path(folder) / "raw.json",
                None,
                0,
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["valid_reviews"], 51)
            sorter.assert_not_called()


class FileTests(unittest.TestCase):
    def setUp(self):
        def collect(command, **kwargs):
            from storage import save_result

            raw = Path(command[command.index("--output") + 1])
            snapshot, _ = example()
            save_result(raw / f"{OID}.json", snapshot)
            return subprocess.CompletedProcess(command, 0)

        self.browser = self.enterContext(patch("subprocess.run", side_effect=collect))

    @patch("export_homeradar.collect_details")
    def test_browser_failure_cannot_be_replaced_by_public_html(self, fetch):
        _, fetch.return_value = example()
        self.browser.side_effect = None
        self.browser.return_value = subprocess.CompletedProcess([], 1)
        with TemporaryDirectory() as folder:
            target = Path(folder) / "ready.json"
            target.write_text("previous", encoding="utf-8")
            with self.assertRaises(ValueError):
                export.run_export(OID, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "previous")
        fetch.assert_not_called()

    @patch("export_homeradar.collect_details")
    def test_invalid_individual_rating_keeps_existing_export(self, fetch):
        _, details = example()
        details["reviews"][0]["rating"] = None
        fetch.return_value = details
        with TemporaryDirectory() as folder:
            target = Path(folder) / "ready.json"
            target.write_text("previous export", encoding="utf-8")
            with self.assertRaises(ValueError):
                export.run_export(OID, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "previous export")

    @patch("export_homeradar.collect_details")
    def test_live_export_requires_chromium_before_supplementary_html(self, fetch):
        _, details = example()
        fetch.return_value = details
        with TemporaryDirectory() as folder:
            target = Path(folder) / "ready.json"
            rows = export.run_export(OID, target)
            snapshot = json.loads(
                (Path(folder) / "work" / OID / "raw" / f"{OID}.json").read_bytes()
            )
            self.assertEqual(snapshot["reviews"][0]["source_review_id"], "source-1")
            self.assertEqual(rows[0]["text"], details["reviews"][0]["text"])
            self.assertEqual(set(rows[0]), FIELDS)
        self.browser.assert_called_once()
        command = self.browser.call_args.args[0]
        self.assertTrue(command[1].endswith("collect_yandex.py"))
        self.assertIn("--refresh", command)
        self.assertEqual(command[command.index("--429-retries") + 1], "0")

    @patch("export_homeradar.collect_details")
    def test_source_timeout_preserves_previous_file(self, run):
        run.side_effect = TimeoutError("source timed out")
        with TemporaryDirectory() as folder:
            target = Path(folder) / "ready.json"
            target.write_text("old", encoding="utf-8")
            with self.assertRaises(TimeoutError):
                export.run_export(OID, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_refuses_overwriting_an_input_snapshot(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "raw.json"
            target.write_text("old", encoding="utf-8")
            with self.assertRaises(ValueError):
                export.run_export(
                    OID, target, snapshot_path=target, details_path=target
                )
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_offline_cli_writes_utf8_nine_field_array(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot, details = example()
            (root / "raw.json").write_text(json.dumps(snapshot), encoding="utf-8")
            (root / "details.json").write_text(json.dumps(details), encoding="utf-8")
            argv = [
                "export_homeradar.py",
                OID,
                "--output",
                str(root / "ready.json"),
                "--from-snapshot",
                str(root / "raw.json"),
                "--details",
                str(root / "details.json"),
            ]
            with patch.object(sys, "argv", argv):
                export.main()
            rows = json.loads((root / "ready.json").read_bytes())
            self.assertEqual(set(rows[0]), FIELDS)
            self.assertEqual(rows[0]["text"], "Отличный дом!")

    def test_failed_validation_preserves_previous_ready_file(self):
        snapshot, details = example()
        snapshot["status"] = "captcha"
        with TemporaryDirectory() as folder:
            target = Path(folder) / "ready.json"
            target.write_text("previous successful export", encoding="utf-8")
            with self.assertRaises(ValueError):
                export.write_export(snapshot, details, target)
            self.assertEqual(
                target.read_text(encoding="utf-8"), "previous successful export"
            )

    @patch("export_homeradar.collect_details")
    def test_failed_source_preserves_previous_export(self, run):
        run.side_effect = ValueError("incomplete source")
        with TemporaryDirectory() as folder:
            target = Path(folder) / "ready.json"
            target.write_text("old", encoding="utf-8")
            with self.assertRaises(ValueError):
                export.run_export(OID, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            run.assert_called_once_with(OID)


if __name__ == "__main__":
    unittest.main()
