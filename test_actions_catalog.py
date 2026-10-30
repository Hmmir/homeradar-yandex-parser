"""Read-only catalog discovery with fictional pages and no external requests."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

import actions_catalog as catalog


SITEMAP = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://homeradar.ru/stroitelstvo/one/</loc></url>
<url><loc>https://homeradar.ru/stroitelstvo/two/</loc></url>
<url><loc>https://homeradar.ru/stroitelstvo/three/</loc></url>
</urlset>"""


class CatalogTests(unittest.TestCase):
    def test_extracts_only_valid_yandex_organization_links(self):
        html = """<a href="https://yandex.ru/maps/org/12345/reviews/">Reviews</a>
        <a href="https://yandex.ru/maps/org/test/12345/">Duplicate</a>
        <a href="https://yandex.ru.evil/maps/org/999/">Untrusted</a>
        <a href="http://yandex.ru/maps/org/998/">Insecure</a>"""
        self.assertEqual(catalog.organization_ids(html), ["12345"])

    @patch("actions_catalog.time.sleep")
    @patch("actions_catalog.fetch")
    def test_discovers_unique_ids_and_preserves_alias_urls(self, fetch, sleep):
        fetch.side_effect = [
            SITEMAP,
            '<a href="https://yandex.ru/maps/org/12345/reviews/">x</a>',
            '<a href="https://yandex.ru/maps/org/12345/">x</a>',
            '<a href="https://yandex.ru/maps/org/67890/reviews/">x</a>',
        ]
        with TemporaryDirectory() as root:
            output = Path(root)
            result = catalog.discover_catalog(output)
            saved = json.loads((output / "catalog.json").read_bytes())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["organizations"]), 2)
        self.assertEqual(len(result["organizations"][0]["site_urls"]), 2)
        self.assertEqual(result, saved)
        self.assertEqual(result["pages_checked"], 3)

    @patch("actions_catalog.time.sleep")
    @patch("actions_catalog.fetch")
    def test_missing_mapping_and_redirect_error_are_reported_not_hidden(
        self, fetch, sleep
    ):
        fetch.side_effect = [
            SITEMAP,
            "<html>No link</html>",
            HTTPError(
                "https://homeradar.ru/stroitelstvo/two/", 301, "redirect", {}, None
            ),
            '<a href="https://yandex.ru/maps/org/67890/">x</a>',
        ]
        with TemporaryDirectory() as root:
            result = catalog.discover_catalog(Path(root))
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(len(result["organizations"]), 1)

    @patch("actions_catalog.time.sleep")
    @patch("actions_catalog.fetch")
    def test_access_restriction_stops_discovery(self, fetch, sleep):
        fetch.side_effect = [
            SITEMAP,
            HTTPError(
                "https://homeradar.ru/stroitelstvo/one/", 429, "PRIVATE", {}, None
            ),
        ]
        with TemporaryDirectory() as root:
            result = catalog.discover_catalog(Path(root))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["status"], "access_restricted")
        self.assertNotIn("PRIVATE", json.dumps(result))

    @patch("actions_catalog.fetch")
    def test_rejects_external_site_urls_before_fetching_them(self, fetch):
        fetch.return_value = SITEMAP.replace(
            "https://homeradar.ru/stroitelstvo/one/", "http://127.0.0.1/private"
        )
        with TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                catalog.discover_catalog(Path(root))
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
