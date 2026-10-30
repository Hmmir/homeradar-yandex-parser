"""Read-only discovery of every published HomeRadar company and its mapping."""

import json
import re
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from actions_smoke import ROOT, publish_summary, run_suite
from collect_yandex import canonical_url
from storage import save_result


WORDPRESS_API = "https://homeradar.ru/wp-json/wp/v2/company"
PER_PAGE = 100
OUTPUT = ROOT / "review_results" / "actions-catalog"


class CatalogValidationError(ValueError):
    """Only fixed diagnostic codes, never untrusted response content."""


def wordpress_page_url(page):
    if type(page) is not int or page < 1:
        raise CatalogValidationError("invalid_wordpress_page")
    return (
        f"{WORDPRESS_API}?status=publish&per_page={PER_PAGE}&page={page}"
        "&orderby=id&order=asc&_fields=id,link,slug"
    )


def validate_company_url(url):
    if not isinstance(url, str) or any(
        char.isspace() or ord(char) < 32 for char in url
    ):
        raise CatalogValidationError("invalid_company_url")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise CatalogValidationError("invalid_company_url") from None
    path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "homeradar.ru"
        or parsed.query
        or parsed.fragment
        or "\\" in path
        or not re.fullmatch(r"/stroitelstvo/[^/]+/", path)
        or path.split("/")[2] in (".", "..")
        or any(char.isspace() or ord(char) < 32 for char in path)
    ):
        raise CatalogValidationError("invalid_company_url")


class HomeRadarRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Registry pages are constructed locally; Location cannot alter pagination.
        if urlsplit(req.full_url).path == urlsplit(WORDPRESS_API).path:
            raise CatalogValidationError("unexpected_wordpress_redirect")
        validate_company_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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


def fetch_response(url, *, registry=False):
    if not registry:
        validate_company_url(url)
    request = Request(url, headers={"User-Agent": "HomeRadarAcceptance/1.0"})
    with build_opener(HomeRadarRedirectHandler()).open(request, timeout=30) as response:
        if response.status != 200:
            raise CatalogValidationError("unexpected_http_status")
        if registry:
            if response.url != url:
                raise CatalogValidationError("unexpected_wordpress_redirect")
        else:
            validate_company_url(response.url)
        content = response.read(10_000_001)
        if len(content) > 10_000_000:
            raise CatalogValidationError("response_too_large")
        return content.decode("utf-8"), response.headers


def fetch(url):
    return fetch_response(url)[0]


def fetch_wordpress_page(page):
    content, headers = fetch_response(wordpress_page_url(page), registry=True)
    totals = [headers.get(name) for name in ("X-WP-Total", "X-WP-TotalPages")]
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value)
        for value in totals
    ):
        raise CatalogValidationError("invalid_wordpress_totals")
    return json.loads(content), int(totals[0]), int(totals[1])


def error_details(error):
    item = {"error_type": type(error).__name__}
    if isinstance(error, CatalogValidationError):
        item["reason"] = str(error)
    if isinstance(error, HTTPError):
        item["http_status"] = error.code
    return item


def discover_catalog(output):
    source = wordpress_page_url(1)
    report = {
        "source": source,
        "status": "running",
        "site_pages": 0,
        "pages_checked": 0,
        "wordpress": {
            "source": source,
            "total_posts": None,
            "total_pages": None,
            "pages_fetched": 0,
            "posts_received": 0,
        },
        "companies": [],
        "organizations": [],
        "errors": [],
    }
    save_result(output / "catalog.json", report)
    page = 1
    post_ids, site_urls = set(), set()
    registry = report["wordpress"]
    try:
        while True:
            posts, total, pages = fetch_wordpress_page(page)
            registry["pages_fetched"] += 1
            if not isinstance(posts, list):
                raise CatalogValidationError("invalid_wordpress_posts")
            registry["posts_received"] += len(posts)
            if (
                type(total) is not int
                or type(pages) is not int
                or total < 1
                or pages != (total + PER_PAGE - 1) // PER_PAGE
            ):
                raise CatalogValidationError("invalid_wordpress_totals")
            if page == 1:
                registry.update(total_posts=total, total_pages=pages)
                report["site_pages"] = total
            elif (total, pages) != (registry["total_posts"], registry["total_pages"]):
                raise CatalogValidationError("wordpress_totals_changed")
            if len(posts) != min(PER_PAGE, total - (page - 1) * PER_PAGE):
                raise CatalogValidationError("wordpress_post_count_mismatch")
            for post in posts:
                if (
                    not isinstance(post, dict)
                    or type(post.get("id")) is not int
                    or post["id"] < 1
                ):
                    raise CatalogValidationError("invalid_wordpress_post_id")
                if "status" in post and post["status"] != "publish":
                    raise CatalogValidationError("unpublished_wordpress_post")
                company_id, url, slug = post["id"], post.get("link"), post.get("slug")
                validate_company_url(url)
                if (
                    not isinstance(slug, str)
                    or urlsplit(url).path != f"/stroitelstvo/{slug}/"
                ):
                    raise CatalogValidationError("invalid_wordpress_slug")
                if company_id in post_ids:
                    raise CatalogValidationError("wordpress_duplicate_post_id")
                if url in site_urls:
                    raise CatalogValidationError("wordpress_duplicate_company_url")
                if (
                    report["companies"]
                    and company_id <= report["companies"][-1]["company_id"]
                ):
                    raise CatalogValidationError("wordpress_post_order_mismatch")
                post_ids.add(company_id)
                site_urls.add(url)
                report["companies"].append(
                    {"company_id": company_id, "site_url": url, "organization_id": None}
                )
            save_result(output / "catalog.json", report)
            if page == pages:
                break
            page += 1
        if len(report["companies"]) != registry["total_posts"]:
            raise CatalogValidationError("wordpress_post_count_mismatch")
    except (OSError, URLError, ValueError) as error:
        report["status"] = (
            "access_restricted"
            if isinstance(error, HTTPError) and error.code in (401, 403, 429)
            else "failed"
        )
        report["errors"].append(
            {"stage": "wordpress_registry", "page": page, **error_details(error)}
        )
        save_result(output / "catalog.json", report)
        return report

    by_id = {}
    for index, company in enumerate(report["companies"]):
        if index:
            time.sleep(0.5)
        url = company["site_url"]
        context = {"company_id": company["company_id"], "site_url": url}
        try:
            ids = organization_ids(fetch(url))
            if len(ids) != 1:
                report["errors"].append(
                    {
                        **context,
                        "reason": "organization_id_count",
                        "count": len(ids),
                    }
                )
            else:
                company["organization_id"] = ids[0]
                if ids[0] not in by_id:
                    organization = {
                        "organization_id": ids[0],
                        "company_ids": [],
                        "site_urls": [],
                    }
                    by_id[ids[0]] = organization
                    report["organizations"].append(organization)
                by_id[ids[0]]["company_ids"].append(company["company_id"])
                by_id[ids[0]]["site_urls"].append(url)
        except (OSError, URLError, ValueError) as error:
            report["errors"].append({**context, **error_details(error)})
            if isinstance(error, HTTPError) and error.code in (401, 403, 429):
                report["status"] = "access_restricted"
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
