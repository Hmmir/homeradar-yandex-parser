"""One-off, read-only acceptance run of public HomeRadar organization mappings."""

import json
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from actions_smoke import ROOT, publish_summary, run_suite
from collect_yandex import canonical_url
from storage import save_result


SITEMAP = "https://homeradar.ru/company-sitemap.xml"
OUTPUT = ROOT / "review_results" / "actions-catalog"


class OrganizationLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if not href.startswith("https://"):
            return
        try:
            oid, _ = canonical_url(href)
        except ValueError:
            return
        if oid not in self.ids:
            self.ids.append(oid)


def organization_ids(html):
    parser = OrganizationLinks()
    parser.feed(html)
    return parser.ids


def fetch(url):
    request = Request(url, headers={"User-Agent": "HomeRadarAcceptance/1.0"})
    with urlopen(request, timeout=30) as response:
        final = urlsplit(response.url)
        if final.scheme != "https" or final.netloc != "homeradar.ru":
            raise ValueError("Unexpected HomeRadar redirect")
        content = response.read(10_000_001)
        if len(content) > 10_000_000:
            raise ValueError("HomeRadar response is too large")
        return content.decode("utf-8")


def discover_catalog(output):
    sitemap = ET.fromstring(fetch(SITEMAP))
    urls = list(
        dict.fromkeys(
            node.text for node in sitemap.findall("{*}url/{*}loc") if node.text
        )
    )
    if not urls:
        raise ValueError("Empty company sitemap")
    for url in urls:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "homeradar.ru"
            or not parsed.path.startswith("/stroitelstvo/")
        ):
            raise ValueError("Unexpected URL in the company sitemap")
    report = {
        "source": SITEMAP,
        "status": "running",
        "site_pages": len(urls),
        "pages_checked": 0,
        "organizations": [],
        "errors": [],
    }
    by_id = {}
    save_result(output / "catalog.json", report)
    for index, url in enumerate(urls):
        if index:
            time.sleep(0.5)
        try:
            ids = organization_ids(fetch(url))
            if len(ids) != 1:
                report["errors"].append(
                    {
                        "site_url": url,
                        "reason": "organization_id_count",
                        "count": len(ids),
                    }
                )
            else:
                if ids[0] not in by_id:
                    organization = {"organization_id": ids[0], "site_urls": []}
                    by_id[ids[0]] = organization
                    report["organizations"].append(organization)
                by_id[ids[0]]["site_urls"].append(url)
        except (OSError, URLError, ValueError) as error:
            item = {"site_url": url, "error_type": type(error).__name__}
            if isinstance(error, HTTPError):
                item["http_status"] = error.code
                if error.code in (401, 403, 429):
                    report["status"] = "access_restricted"
            report["errors"].append(item)
        report["pages_checked"] += 1
        save_result(output / "catalog.json", report)
        if report["status"] == "access_restricted":
            return report
    report["status"] = "complete"
    save_result(output / "catalog.json", report)
    return report


def main():
    OUTPUT.mkdir(parents=True, exist_ok=False)
    catalog = discover_catalog(OUTPUT)
    print(
        json.dumps(
            {
                "event": "catalog_discovery",
                "status": catalog["status"],
                "site_pages": catalog["site_pages"],
                "organizations": len(catalog["organizations"]),
                "page_errors": len(catalog["errors"]),
            }
        ),
        flush=True,
    )
    if catalog["status"] != "complete" or not catalog["organizations"]:
        return 1
    cases = tuple(item["organization_id"] for item in catalog["organizations"])
    report = run_suite(OUTPUT / "results", cases)
    publish_summary(OUTPUT / "results")
    return 0 if report["passed"] == len(cases) and not catalog["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
