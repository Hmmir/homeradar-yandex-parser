"""Public WordPress fixtures; these tests never contact either website."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import actions_catalog as catalog


def company(index, **changes):
    return {
        "id": index,
        "link": f"https://homeradar.ru/stroitelstvo/company-{index}/",
        "slug": f"company-{index}",
        **changes,
    }


def link(oid):
    return f'<a href="https://yandex.ru/maps/org/{oid}/reviews/">x</a>'


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.fetch = self.enterContext(patch("actions_catalog.fetch"))
        self.registry = self.enterContext(
            patch.object(catalog, "fetch_wordpress_page", create=True)
        )
        self.sleep = self.enterContext(patch("actions_catalog.time.sleep"))
        self.registry.return_value = ([company(1)], 1, 1)
        self.fetch.return_value = link("12345")

    def discover(self):
        with TemporaryDirectory() as root:
            output = Path(root)
            result = catalog.discover_catalog(output)
            self.assertEqual(result, json.loads((output / "catalog.json").read_bytes()))
            self.assertEqual(list(output.glob("*.tmp")), [])
            return result

    def test_extracts_only_valid_distinct_https_yandex_organization_links(self):
        html = (
            link("12345")
            + """
        <a href="https://yandex.com/maps/org/test/12345/">Duplicate</a>
        <a href="https://yandex.ru.evil/maps/org/999/">Untrusted</a>
        <a href="http://yandex.ru/maps/org/998/">Insecure</a>
        <a href="123456">Raw ID is not a public link</a>"""
        )
        self.assertEqual(catalog.organization_ids(html), ["12345"])

    def test_wordpress_registry_includes_all_258_posts_not_220_sitemap_urls(self):
        posts = [company(index) for index in range(1, 259)]
        self.registry.side_effect = [
            (posts[:100], 258, 3),
            (posts[100:200], 258, 3),
            (posts[200:], 258, 3),
        ]
        result = self.discover()
        self.assertEqual(result["status"], "complete")
        self.assertEqual((result["site_pages"], result["pages_checked"]), (258, 258))
        self.assertEqual(result["organizations"][0]["company_ids"], list(range(1, 259)))
        self.assertEqual(self.registry.call_args_list, [call(1), call(2), call(3)])
        self.assertEqual(
            self.fetch.call_args_list, [call(post["link"]) for post in posts]
        )
        self.assertEqual(len(result["companies"][220:]), 38)
        self.assertEqual(
            result["companies"][-1],
            {
                "company_id": 258,
                "site_url": posts[-1]["link"],
                "organization_id": "12345",
            },
        )
        self.assertEqual(
            result["wordpress"],
            {
                "source": result["source"],
                "total_posts": 258,
                "total_pages": 3,
                "pages_fetched": 3,
                "posts_received": 258,
            },
        )

    def test_preserves_all_alias_company_ids_and_urls_without_duplicate_collection(
        self,
    ):
        posts = [company(index) for index in range(1, 4)]
        self.registry.return_value = (posts, 3, 1)
        self.fetch.side_effect = [link("12345"), link("12345"), link("67890")]
        result = self.discover()
        self.assertEqual(
            result["organizations"],
            [
                {
                    "organization_id": "12345",
                    "company_ids": [1, 2],
                    "site_urls": [posts[0]["link"], posts[1]["link"]],
                },
                {
                    "organization_id": "67890",
                    "company_ids": [3],
                    "site_urls": [posts[2]["link"]],
                },
            ],
        )
        self.assertEqual(
            [row["organization_id"] for row in result["companies"]],
            ["12345", "12345", "67890"],
        )

    def test_company_page_requests_are_sequential_and_half_second_apart(self):
        self.registry.return_value = ([company(1), company(2), company(3)], 3, 1)
        events = []
        self.fetch.side_effect = lambda url: events.append(url) or link("12345")
        self.sleep.side_effect = lambda seconds: events.append(seconds)
        self.discover()
        self.assertEqual(
            events,
            [company(1)["link"], 0.5, company(2)["link"], 0.5, company(3)["link"]],
        )

    def test_progress_is_atomically_saved_after_registry_and_each_company_page(self):
        self.registry.return_value = ([company(1), company(2)], 2, 1)
        snapshots = []
        writer = catalog.save_result

        def save(target, report):
            snapshots.append(json.loads(json.dumps(report)))
            writer(target, report)

        with patch("actions_catalog.save_result", side_effect=save):
            result = self.discover()
        self.assertEqual(snapshots[0]["status"], "running")
        self.assertIn(1, [row["pages_checked"] for row in snapshots])
        self.assertEqual(snapshots[-1], result)

    def test_missing_multiple_ids_and_redirect_loop_are_reported_not_omitted(self):
        self.registry.return_value = ([company(i) for i in range(1, 5)], 4, 1)
        self.fetch.side_effect = [
            "<html>No link</html>",
            link("12345") + link("67890"),
            HTTPError(company(3)["link"], 301, "PRIVATE loop", {}, None),
            link("67890"),
        ]
        result = self.discover()
        self.assertEqual(result["pages_checked"], 4)
        self.assertEqual([row["company_id"] for row in result["errors"]], [1, 2, 3])
        self.assertEqual([row.get("count") for row in result["errors"][:2]], [0, 2])
        self.assertEqual(result["errors"][2]["http_status"], 301)
        self.assertEqual(
            [row["organization_id"] for row in result["companies"]],
            [None, None, None, "67890"],
        )
        self.assertEqual(result["organizations"][0]["company_ids"], [4])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_company_http_access_restrictions_stop_all_remaining_lookups(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                self.fetch.reset_mock()
                self.registry.return_value = ([company(1), company(2)], 2, 1)
                self.fetch.side_effect = HTTPError(
                    company(1)["link"], status, "PRIVATE", {}, None
                )
                result = self.discover()
                self.assertEqual(self.fetch.call_count, 1)
                self.assertEqual(result["pages_checked"], 1)
                self.assertEqual(result["status"], "access_restricted")
                self.assertEqual(len(result["companies"]), 2)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_registry_access_restrictions_stop_before_any_company_requests(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                self.registry.reset_mock()
                self.registry.side_effect = HTTPError(
                    "https://homeradar.ru/wp-json/wp/v2/company",
                    status,
                    "PRIVATE",
                    {},
                    None,
                )
                result = self.discover()
                self.assertEqual(result["status"], "access_restricted")
                self.assertEqual(self.registry.call_count, 1)
                self.fetch.assert_not_called()
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_registry_network_error_is_private_and_saved_as_failed(self):
        self.registry.side_effect = URLError("PRIVATE token")
        result = self.discover()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.fetch.assert_not_called()

    def test_changing_totals_or_page_counts_fail_before_mapping_any_company(self):
        for total, pages in ((102, 2), (101, 3)):
            with self.subTest(total=total, pages=pages):
                self.registry.side_effect = [
                    ([company(i) for i in range(1, 101)], 101, 2),
                    ([company(101)], total, pages),
                ]
                result = self.discover()
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["errors"])
                self.assertEqual(result["wordpress"]["posts_received"], 101)
                self.fetch.assert_not_called()

    def test_duplicate_post_ids_across_pages_fail_without_partial_collection(self):
        self.registry.side_effect = [
            ([company(i) for i in range(1, 101)], 101, 2),
            ([company(100, link=company(101)["link"], slug="company-101")], 101, 2),
        ]
        result = self.discover()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["reason"], "wordpress_duplicate_post_id")
        self.fetch.assert_not_called()

    def test_duplicate_urls_are_rejected_instead_of_merging_different_posts(self):
        self.registry.return_value = (
            [company(1), company(2, link=company(1)["link"], slug="company-1")],
            2,
            1,
        )
        result = self.discover()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["errors"][0]["reason"], "wordpress_duplicate_company_url"
        )
        self.fetch.assert_not_called()

    def test_invalid_registry_data_never_reaches_company_fetch(self):
        invalid_pages = [
            ([], 0, 0),
            ([company(1)], 2, 1),
            ([company(1)], 1, 2),
            ({"code": "rest_forbidden"}, 1, 1),
            ([None], 1, 1),
            ([company(True)], 1, 1),
            ([company(0)], 1, 1),
            ([company(1, status="draft")], 1, 1),
            ([company(1, slug="different")], 1, 1),
            ([company(1)], True, 1),
            ([company(1)], 1, False),
            ([company(2), company(1)], 2, 1),
        ]
        for page in invalid_pages:
            with self.subTest(page=page):
                self.registry.return_value = page
                result = self.discover()
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["errors"])
                self.fetch.assert_not_called()

    def test_unsafe_or_non_company_urls_are_rejected_before_fetching_them(self):
        for url in (
            "http://127.0.0.1/private",
            "https://homeradar.ru.evil/stroitelstvo/company-1/",
            "http://homeradar.ru/stroitelstvo/company-1/",
            "https://user:PRIVATE@homeradar.ru/stroitelstvo/company-1/",
            "https://homeradar.ru:443/stroitelstvo/company-1/",
            "https://homeradar.ru/wp-admin/",
            "https://homeradar.ru/stroitelstvo/",
            "https://homeradar.ru/stroitelstvo/../wp-admin/",
            "https://homeradar.ru/stroitelstvo/%2fwp-admin/",
            "https://homeradar.ru/stroitelstvo/company-1/?token=PRIVATE",
            "https://homeradar.ru/stroitelstvo/company-1/#PRIVATE",
            "https://homeradar.ru/stroitelstvo/company-1/\n",
        ):
            with self.subTest(url=url):
                self.registry.return_value = ([company(1, link=url)], 1, 1)
                result = self.discover()
                self.assertEqual(result["status"], "failed")
                self.fetch.assert_not_called()
                self.assertNotIn("PRIVATE", json.dumps(result))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.opener_factory = self.enterContext(
            patch.object(catalog, "build_opener", create=True)
        )
        self.response = MagicMock()
        self.response.status = 200
        self.response.url = (
            "https://homeradar.ru/wp-json/wp/v2/company?status=publish&per_page=100"
            "&page=1&orderby=id&order=asc&_fields=id,link,slug"
        )
        self.response.headers = {"X-WP-Total": "1", "X-WP-TotalPages": "1"}
        self.response.read.return_value = json.dumps([company(1)]).encode()
        self.opener_factory.return_value.open.return_value.__enter__.return_value = (
            self.response
        )

    def test_wordpress_request_has_fixed_endpoint_fields_totals_and_limits(self):
        self.assertEqual(catalog.fetch_wordpress_page(1), ([company(1)], 1, 1))
        args, kwargs = self.opener_factory.return_value.open.call_args
        request = args[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual(
            (parsed.scheme, parsed.netloc, parsed.path),
            ("https", "homeradar.ru", "/wp-json/wp/v2/company"),
        )
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "status": ["publish"],
                "per_page": ["100"],
                "page": ["1"],
                "orderby": ["id"],
                "order": ["asc"],
                "_fields": ["id,link,slug"],
            },
        )
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(request.get_method(), "GET")
        self.response.read.assert_called_once_with(10_000_001)

    def test_missing_or_malformed_wordpress_totals_are_rejected(self):
        for headers in (
            {},
            {"X-WP-Total": "1"},
            {"X-WP-Total": "-1", "X-WP-TotalPages": "1"},
            {"X-WP-Total": "PRIVATE", "X-WP-TotalPages": "1"},
        ):
            with self.subTest(headers=headers):
                self.response.headers = headers
                with self.assertRaises(ValueError):
                    catalog.fetch_wordpress_page(1)

    def test_invalid_page_numbers_are_rejected_without_network_requests(self):
        for page in (0, -1, True, "1", 1.5):
            with self.subTest(page=page):
                with self.assertRaises(ValueError):
                    catalog.fetch_wordpress_page(page)
        self.opener_factory.return_value.open.assert_not_called()

    def test_registry_redirect_or_partial_response_cannot_change_pagination(self):
        original = self.response.url
        for final_url, status in (
            ("https://evil.example/private", 200),
            (original.replace("&page=1", "&page=2"), 200),
            (original, 206),
        ):
            with self.subTest(final_url=final_url, status=status):
                self.response.url = final_url
                self.response.status = status
                with self.assertRaises(ValueError):
                    catalog.fetch_wordpress_page(1)

    def test_response_size_limit_is_preserved(self):
        self.response.read.return_value = b"x" * 10_000_001
        with self.assertRaises(ValueError):
            catalog.fetch_wordpress_page(1)

    def test_malformed_json_is_rejected(self):
        self.response.read.return_value = b"not JSON"
        with self.assertRaises(ValueError):
            catalog.fetch_wordpress_page(1)

    def test_redirect_handler_prevents_external_requests_and_pagination_escape(self):
        handler = catalog.HomeRadarRedirectHandler()
        request = Request(company(1)["link"])
        for url in (
            "http://127.0.0.1/private",
            "https://evil.example/private",
            "https://homeradar.ru/wp-admin/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    handler.redirect_request(request, None, 302, "Found", {}, url)
        with self.assertRaises(ValueError):
            handler.redirect_request(
                Request(self.response.url),
                None,
                302,
                "Found",
                {},
                self.response.url.replace("&page=1", "&page=2"),
            )

    def test_company_fetch_rejects_unsafe_input_before_opening_a_connection(self):
        for url in (
            "http://127.0.0.1/private",
            "https://evil.example/private",
            "https://homeradar.ru/wp-admin/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    catalog.fetch(url)
        self.opener_factory.return_value.open.assert_not_called()

    def test_company_fetch_checks_final_host_and_company_path(self):
        for url in (
            "https://evil.example/private",
            "http://homeradar.ru/private",
            "https://homeradar.ru/wp-admin/",
        ):
            with self.subTest(url=url):
                self.response.url = url
                with self.assertRaises(ValueError):
                    catalog.fetch(company(1)["link"])

    def test_company_fetch_returns_html_after_validated_same_site_redirect(self):
        self.response.url = company(2)["link"]
        self.response.read.return_value = link("12345").encode()
        self.assertEqual(catalog.fetch(company(1)["link"]), link("12345"))

    def test_company_redirects_remain_inside_validated_company_path(self):
        redirect = catalog.HomeRadarRedirectHandler().redirect_request(
            Request(company(1)["link"]), None, 301, "Moved", {}, company(2)["link"]
        )
        self.assertEqual(redirect.full_url, company(2)["link"])


if __name__ == "__main__":
    unittest.main()
