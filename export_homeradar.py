"""Collect Yandex Maps reviews and export verified data for HomeRadar."""

import argparse
import json
import math
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from collect_yandex import cache_complete, canonical_url, run_cli
from extract_saved_html import StateScript
from storage import output_lock, save_result


def read_business(html, oid):
    """Reuse the upstream HTML parser; never interpret missing data as no reviews."""
    parser = StateScript()
    parser.feed(html)
    state = json.loads("".join(parser.parts))
    if not isinstance(state, dict) or not isinstance(state.get("stack"), list):
        raise ValueError(
            "Нет состояния карточки Яндекса: возможна CAPTCHA или изменение HTML"
        )
    for panel in state["stack"]:
        for item in panel.get("results", {}).get("items", []):
            if str(item.get("id")) == oid and isinstance(
                item.get("reviewResults"), dict
            ):
                return item
    raise ValueError(f"В HTML не найдены отзывы организации {oid}")


def fetch_business(oid, page):
    request = Request(
        f"https://yandex.ru/maps/org/{oid}/reviews/?page={page}",
        headers={"User-Agent": "HomeRadarExport/1.0", "Accept-Language": "ru-RU"},
    )
    with urlopen(request, timeout=30) as response:
        if urlsplit(response.url).hostname not in {"yandex.ru", "yandex.com"}:
            raise ValueError("Яндекс перенаправил запрос за пределы карточки")
        html = response.read(20_000_001)
        if len(html) > 20_000_000:
            raise ValueError("Слишком большой HTML-ответ Яндекса")
        return read_business(html.decode("utf-8"), oid)


def metrics(ratings):
    if not isinstance(ratings, dict):
        raise ValueError("Нет общих показателей организации")
    rating, count, reviews = (
        ratings.get(k) for k in ("ratingValue", "ratingCount", "reviewCount")
    )
    if (
        type(rating) not in (int, float)
        or not math.isfinite(rating)
        or not 1 <= rating <= 5
    ):
        raise ValueError("Общий рейтинг организации отсутствует или некорректен")
    if any(type(n) is not int or n < 0 for n in (count, reviews)):
        raise ValueError("Количество оценок или отзывов отсутствует или некорректно")
    return round(rating, 1), count, reviews


def validate_details(details, oid):
    if not isinstance(details, dict) or details.get("organization_id") != oid:
        raise ValueError("Дополнительные поля относятся к другой организации")
    pages, reviews = details.get("pages"), details.get("reviews")
    if not isinstance(pages, list) or not pages or not isinstance(reviews, list):
        raise ValueError("Отсутствуют страницы или отзывы дополнительной выгрузки")
    if any(
        not isinstance(page, dict) or not isinstance(page.get("pagination"), dict)
        for page in pages
    ):
        raise ValueError("Некорректная структура страниц дополнительной выгрузки")
    expected = metrics(pages[0].get("rating"))
    for number, page in enumerate(pages, 1):
        params = page.get("pagination", {})
        if (
            params.get("page") != number
            or type(params.get("totalPages")) is not int
            or not 1 <= params["totalPages"] <= 12
            or params["totalPages"] != len(pages)
            or params.get("count") != expected[2]
            or metrics(page.get("rating")) != expected
        ):
            raise ValueError(
                "Неполная пагинация или показатели изменились во время сбора"
            )
    ids = set()
    for row in reviews:
        if (
            not isinstance(row, dict)
            or str(row.get("businessId")) != oid
            or not isinstance(row.get("reviewId"), str)
            or not row["reviewId"]
            or row["reviewId"] in ids
        ):
            raise ValueError("Неверная организация, отсутствующий ID или дубли отзывов")
        ids.add(row["reviewId"])
    if len(reviews) != expected[2]:
        raise ValueError("Число полученных отзывов не совпадает со счётчиком Яндекса")
    return expected


def collect_details(oid):
    """Read public HTML pages for avatars and replies omitted by the upstream export."""
    details = {"organization_id": oid, "pages": [], "reviews": []}
    total = 1
    number = 1
    while number <= total:
        if number > 1:
            time.sleep(1)
        business = fetch_business(oid, number)
        result = business["reviewResults"]
        params = result.get("params", {})
        reported_total = params.get("totalPages")
        if type(reported_total) is not int or not 1 <= reported_total <= 12:
            raise ValueError("Яндекс не отдаёт полную выгрузку в пределах 12 страниц")
        if params.get("page") != number or (number > 1 and reported_total != total):
            raise ValueError("Яндекс вернул другую страницу или изменил пагинацию")
        total = reported_total
        details["pages"].append(
            {"rating": business.get("ratingData"), "pagination": params}
        )
        rows = result.get("reviews")
        if not isinstance(rows, list):
            raise ValueError("В ответе Яндекса отсутствует список отзывов")
        details["reviews"].extend(rows)
        number += 1
    validate_details(details, oid)
    return details


def text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Ожидалось строковое поле отзыва")
    return value


def utc_date(value):
    value = text(value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Некорректная ISO-дата отзыва или ответа") from error
    if parsed.tzinfo is None:
        raise ValueError("Дата должна содержать часовой пояс")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def review_identity(row, source=False):
    author = row.get("author")
    if source:
        if author is not None and not isinstance(author, dict):
            raise ValueError("Некорректные данные автора")
        author = (author or {}).get("name")
    author = " ".join(unicodedata.normalize("NFC", text(author)).split())
    if author == "Анонимный отзыв":
        author = ""
    body = " ".join(unicodedata.normalize("NFC", text(row.get("text"))).split())
    rating = row.get("rating")
    if not body or type(rating) is not int or not 1 <= rating <= 5:
        raise ValueError("Отзыв без текста или корректной индивидуальной оценки")
    return body, rating, author, utc_date(row.get("updatedTime" if source else "date"))


def build_export(snapshot, details):
    if not isinstance(snapshot, dict):
        raise ValueError("Снимок сборщика должен быть JSON-объектом")
    oid = snapshot.get("organization_id")
    if not isinstance(oid, str) or not oid.isascii() or not oid.isdigit():
        raise ValueError("В снимке отсутствует числовой ID организации")
    if not cache_complete(snapshot, oid, None):
        raise ValueError(
            "Сбор не завершён полностью; прежний экспорт оставлен без изменений"
        )
    if any(row.get("full_text_confirmed") is not True for row in snapshot["reviews"]):
        raise ValueError("Есть тексты, не подтверждённые текущим сбором")
    rating, ratings_count, reviews_count = validate_details(details, oid)
    company = snapshot["company"]
    if metrics(
        {
            "ratingValue": company.get("rating"),
            "ratingCount": company.get("ratings_count"),
            "reviewCount": company.get("reviews_count"),
        }
    ) != (rating, ratings_count, reviews_count):
        raise ValueError("Показатели браузера и публичных страниц не совпадают")
    if not snapshot["reviews"]:
        raise ValueError(
            "Пустой массив не передаёт рейтинг HomeRadar; прежний файл сохранён"
        )

    by_id = {row["reviewId"]: row for row in details["reviews"]}
    by_content = defaultdict(list)
    for row in details["reviews"]:
        by_content[review_identity(row, source=True)].append(row)
    used = set()
    output = []
    for row in snapshot["reviews"]:
        identity = review_identity(row)
        source_id = row.get("source_review_id")
        matches = [by_id[source_id]] if source_id in by_id else []
        if not source_id:
            matches = by_content.get(identity, [])
        if len(matches) != 1 or review_identity(matches[0], source=True) != identity:
            raise ValueError(
                "Нельзя однозначно сопоставить отзыв с дополнительными полями"
            )
        source = matches[0]
        if source["reviewId"] in used:
            raise ValueError("Один исходный отзыв сопоставлен несколько раз")
        used.add(source["reviewId"])
        author = source.get("author") or {}
        avatar = text(author.get("avatarUrl"))
        if avatar and (
            urlsplit(avatar).scheme != "https" or not urlsplit(avatar).hostname
        ):
            raise ValueError("Аватар должен иметь публичный HTTPS-адрес")
        comment = source.get("businessComment")
        if comment is not None and not isinstance(comment, dict):
            raise ValueError("Некорректный ответ организации")
        comment = comment or {}
        comment_text = text(comment.get("text"))
        output.append(
            {
                "businessRating": rating,
                "businessRatingsCount": ratings_count,
                "text": row["text"],
                "rating": row["rating"],
                "date": identity[3],
                "authorName": text(row.get("author")),
                "authorAvatarUrl": avatar,
                "businessComment": comment_text,
                "businessCommentDate": utc_date(comment.get("updatedTime"))
                if comment_text
                else "",
            }
        )
    if len(used) != len(by_id):
        raise ValueError("Не все исходные отзывы сопоставлены")
    return output


def write_export(snapshot, details, target):
    rows = build_export(snapshot, details)
    save_result(target, rows)
    return rows


def run_export(
    organization, output, executable=None, snapshot_path=None, details_path=None
):
    oid, url = canonical_url(organization)
    output = Path(output).resolve()
    inputs = [Path(p).resolve() for p in (snapshot_path, details_path) if p is not None]
    if output in inputs:
        raise ValueError("Файл экспорта не должен совпадать с исходными файлами")
    with output_lock(output.parent):
        if snapshot_path is None:
            work = output.parent / "work" / oid
            raw = work / "raw"
            snapshot_path = raw / f"{oid}.json"
            details_path = work / "public-details.json"
            if output in (snapshot_path, details_path, raw / "review_jobs.json"):
                raise ValueError(
                    "Файл экспорта не должен совпадать со служебными файлами"
                )
            command = [
                sys.executable,
                str(Path(__file__).with_name("collect_yandex.py")),
                url,
                "--output",
                str(raw),
                "--refresh",
                "--429-retries",
                "0",
            ]
            if executable:
                command.extend(["--executable", str(executable)])
            try:
                result = subprocess.run(command, timeout=900, check=False)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "Сбор превысил 15 минут; прежний экспорт не изменён"
                ) from error
            if result.returncode:
                raise ValueError(
                    f"Сборщик завершился с кодом {result.returncode}; экспорт не изменён"
                )
            details = collect_details(oid)
            save_result(details_path, details)
        else:
            details = json.loads(Path(details_path).read_text(encoding="utf-8-sig"))
        snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8-sig"))
        if not isinstance(snapshot, dict) or snapshot.get("organization_id") != oid:
            raise ValueError("Снимок не относится к запрошенной организации")
        rows = write_export(snapshot, details, output)
        print(
            json.dumps(
                {
                    "event": "homeradar_export",
                    "organization_id": oid,
                    "status": "complete",
                    "file": str(output),
                    "reviews": len(rows),
                    "businessRating": rows[0]["businessRating"],
                    "businessRatingsCount": rows[0]["businessRatingsCount"],
                    "avatars": sum(bool(r["authorAvatarUrl"]) for r in rows),
                    "businessComments": sum(bool(r["businessComment"]) for r in rows),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("organization", help="Числовой ID или HTTPS-ссылка Яндекс Карт")
    parser.add_argument(
        "--output", required=True, type=Path, help="Готовый JSON-массив HomeRadar"
    )
    parser.add_argument("--executable", help="Путь к установленному Chrome/Chromium")
    parser.add_argument(
        "--from-snapshot",
        type=Path,
        help="Повторный экспорт сохранённого снимка без сети",
    )
    parser.add_argument(
        "--details",
        type=Path,
        help="Сохранённый public-details.json для повторного экспорта",
    )
    args = parser.parse_args()
    if (args.from_snapshot is None) != (args.details is None):
        parser.error("--from-snapshot и --details используются только вместе")
    run_export(
        args.organization,
        args.output,
        args.executable,
        args.from_snapshot,
        args.details,
    )


if __name__ == "__main__":
    run_cli(main)
