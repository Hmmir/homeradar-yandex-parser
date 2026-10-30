"""Сбор текстов и оценок из публичных страниц организаций на Яндекс Картах."""

# Подключаем разбор параметров запуска.
import argparse

# Подключаем вычисление ключей SHA-256.
import hashlib

# Подключаем чтение и запись JSON.
import json

# Проверяем конечность задержек и параметров запуска.
import math

# Подключаем проверку адресов и текстовых признаков.
import re

# Подключаем измерение времени и паузы.
import time

# Выводим диагностику ошибок в стандартный поток ошибок.
import sys

# Подключаем дату и время в UTC.
from datetime import datetime, timezone

# Подключаем разбор HTTP-даты.
from email.utils import parsedate_to_datetime

# Подключаем работу с путями и файлами.
from pathlib import Path

# Подключаем разбор адресов страниц.
from urllib.parse import urlsplit

# Подключаем playwright.sync_api.
from playwright.sync_api import (
    # Подключаем playwright.sync_api.
    Error as BrowserError,
    # Подключаем playwright.sync_api.
    TimeoutError as BrowserTimeout,
    # Подключаем управление сессией Chromium.
    sync_playwright,
)

# Подключаем чтение встроенного JSON из HTML.
from extract_saved_html import extract as extract_saved_html

# Повторно используем атомарную запись и сохранение повреждённых снимков.
from storage import save_result, read_snapshot, output_lock

# Наблюдаем завершение загрузок по метаданным обычного браузера.
from page_loads import PageLoads
# Запускаем совместимый браузер с автоматической установкой при отсутствии.
from browser_setup import launch_chromium


# Задаём селектор контейнера отдельного отзыва.
CARDS = ".business-reviews-card-view__review"
# Задаём версию формата сохранённых результатов.
SCHEMA_VERSION = 1
# Отслеживаем последний отзыв даже при замене карточек в виртуализированной выдаче.
TAIL = r"""selector => {
  // Ищем последнюю текущую карточку, не сохраняя устаревший DOM-узел.
  const last = [...document.querySelectorAll(selector)].at(-1);
  // Пустая временная выдача не подтверждает появление нового отзыва.
  if (!last) return null;
  // Сопоставляем дату, профиль и начало текста; число узлов может оставаться прежним.
  return JSON.stringify([
    // Читаем дату последнего отзыва.
    last.querySelector('[itemprop="datePublished"]')?.getAttribute('content'),
    // Читаем публичный профиль, если он доступен.
    last.querySelector('.business-review-view__link')?.getAttribute('href'),
    // Анонимные отзывы различаем также по тексту.
    last.querySelector('[itemprop="reviewBody"], .business-review-view__body')?.textContent.trim().slice(0,160)
  ]);
}"""
# Повторно используем то же определение последнего отзыва при ожидании.
TAIL_CHANGED = f"({{selector,before}}) => {{const current=({TAIL})(selector); return current !== null && current !== before;}}"
# Читаем лёгкие признаки записей для уведомления о новых отзывах в любом месте списка.
DOM_KEYS = r"""selector => [...document.querySelectorAll(selector)].map(card => {
  // Публичный профиль и дата не меняются при раскрытии текста.
  const profile=card.querySelector('.business-review-view__link')?.getAttribute('href') || null;
  // Размеченная дата отличает отзывы одного автора.
  const date=card.querySelector('[itemprop="datePublished"]')?.getAttribute('content') || null;
  // Для анонимных записей используем текст только как уведомление, а не сохранённый ключ.
  const text=profile && date ? null : card.querySelector('[itemprop="reviewBody"], .business-review-view__body')?.textContent?.trim() || null;
  // Возвращаем компактный признак без тела сетевых ответов.
  return JSON.stringify([profile,date,text]);
})"""

# Смена порядка известных записей не является новой подгрузкой.
DOM_PROGRESS = f"({{selector,known}}) => {{const seen=new Set(known); return ({DOM_KEYS})(selector).some(key=>!seen.has(key));}}"

# Выводим конец списка из зоны видимости перед конечным повтором подгрузки.
SCROLL_BACK = r"""selector => {
  // Находим текущую карточку и её прокручиваемую панель.
  const last = [...document.querySelectorAll(selector)].at(-1);
  // Отсутствующий список не требует изменения позиции.
  if (!last) return;
  // Ищем ту же панель, которую использует обычная прокрутка.
  for (let parent=last.parentElement; parent; parent=parent.parentElement) {
    // Пропускаем элементы, которые не прокручиваются.
    if (/auto|scroll/.test(getComputedStyle(parent).overflowY) && parent.scrollHeight > parent.clientHeight) {
      // Отступаем на высоту панели и запас 600 пикселей для зоны наблюдения.
      parent.scrollTop=Math.max(0,parent.scrollTop-parent.clientHeight-600);
      // Другие панели страницы не меняем.
      break;
    }
  }
}"""
# Описываем чтение видимых отзывов внутри браузера.
EXTRACT = r"""limit => {
  // Читаем текст найденного элемента; отсутствующее значение обозначаем null.
  const read = (root, selector) => root.querySelector(selector)?.textContent?.trim() || null;
  // Читаем атрибут content, которым размечены оценки и даты.
  const content = (root, selector) => root.querySelector(selector)?.getAttribute('content') || null;
  // Обрабатываем карточки отзывов в пределах указанного лимита.
  const reviews = [...document.querySelectorAll('.business-reviews-card-view__review')].slice(0, limit ?? undefined).map(card => {
    // Ищем текст только внутри текущего отзыва.
    const body = card.querySelector('[itemprop="reviewBody"], .business-review-view__body');
    // Находим контейнер текста, отделённый от кнопки раскрытия.
    const textNode = body?.querySelector('.spoiler-view__text-container');
    // Читаем текст и убираем пробелы по краям.
    let text = textNode?.textContent?.trim() || null;
    // Если отдельного контейнера нет, читаем копию тела отзыва.
    if (!text && body) {
      // Копируем DOM-узел, сохраняя исходную страницу без изменений.
      const copy = body.cloneNode(true);
      // Удаляем подписи кнопок из копии текста.
      copy.querySelectorAll('.spoiler-view__button, .business-review-view__expand').forEach(e=>e.remove());
      // Читаем очищенный текст из копии узла.
      text = copy.textContent.trim();
    }
    // Ищем оценку, размеченную внутри отдельного отзыва.
    const ratingRaw = content(card, '[itemprop="reviewRating"] [itemprop="ratingValue"]')
      // При необходимости пробуем запасной селектор оценки.
      || content(card, 'meta[itemprop="ratingValue"]');
    // Преобразуем оценку в число; отсутствие оставляем null.
    let rating = ratingRaw === null ? null : Number(ratingRaw);
    // Если числовой оценки нет, проверяем нарисованные звёзды.
    if (rating === null) {
      // Находим звёзды только в блоке оценки текущего отзыва.
      const stars = card.querySelectorAll('.business-review-view__rating .business-rating-badge-view__star');
      // При наличии пяти звёзд считаем заполненные.
      if (stars.length === 5) rating = [...stars].filter(e=>e.classList.contains('_full')).length;
    }
    // Отбрасываем дробные и выходящие за диапазон 1–5 значения.
    if (!Number.isInteger(rating) || rating < 1 || rating > 5) rating = null;
    // Возвращаем извлечённые поля.
    return {
      // Читаем отображаемое имя автора.
      author: read(card, '[itemprop="author"] [itemprop="name"]'),
      // Сохраняем публичную ссылку на профиль автора.
      author_url: card.querySelector('.business-review-view__link')?.getAttribute('href') || null,
      // Читаем дату из HTML-разметки отзыва.
      date: content(card, '[itemprop="datePublished"]'),
      // Передаём текст и индивидуальную оценку.
      text, rating,
      // Проверяем, остался ли текст свёрнутым.
      text_truncated: !!body?.querySelector('.spoiler-view__text._collapsed'),
    };
  });
  // Отдельно читаем общую оценку организации.
  const ratingText = read(document, '.business-summary-rating-badge-view__rating');
  // Приводим текст счётчика к числу, убирая пробелы.
  const number = text => { const m=text?.replace(/\s/g, '').match(/\d+/); return m ? Number(m[0]) : null; };
  // Возвращаем извлечённые поля.
  return {reviews, company: {
    // Читаем название организации.
    name: read(document, '.orgpage-header-view__header'),
    // Преобразуем общую оценку организации в число.
    rating: ratingText ? Number(ratingText.replace(/[^\d,.]/g, '').replace(',', '.')) : null,
    // Читаем число всех оценок, включая оценки без текста.
    ratings_count: number(read(document, '.business-summary-rating-badge-view__rating-count')),
    // Читаем число текстовых отзывов на карточке.
    reviews_count: number(read(document, '.business-reviews-card-view__title')),
  }};
}"""


# Проверяем ссылку и приводим её к странице отзывов организации.
def canonical_url(value):
    # При числовом вводе сразу строим ссылку на отзывы.
    if value.isdigit():
        # Возвращаем идентификатор и канонический адрес страницы отзывов.
        return value, f"https://yandex.ru/maps/org/-/{value}/reviews/"
    # Разбираем URL на схему, домен и путь.
    parsed = urlsplit(value)
    # Проверяем допустимость домена, протокола или типа HTTP-ответа.
    if parsed.scheme != "https" or parsed.hostname not in {"yandex.ru", "yandex.com"}:
        # Сообщаем об ошибке входных или сохранённых данных.
        raise ValueError("Нужна публичная HTTPS-ссылка на организацию в Яндекс Картах")
    # Ищем числовой идентификатор в пути карточки организации.
    match = re.search(r"/maps/org/(?:[^/]+/)?(\d+)(?:/|$)", parsed.path)
    # Отклоняем ссылку без идентификатора организации.
    if not match:
        # Сообщаем об ошибке входных или сохранённых данных.
        raise ValueError("В ссылке не найден числовой идентификатор организации")
    # Извлекаем идентификатор организации из ссылки.
    oid = match.group(1)
    # Возвращаем идентификатор и канонический адрес страницы отзывов.
    return oid, f"https://{parsed.hostname}/maps/org/-/{oid}/reviews/"


def review_text(value):
    # HTML DOM нормализует CRLF/CR в LF; встроенный JSON сохраняет исходные переносы.
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def canonical_profile_url(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if (
        parsed.scheme == "https"
        and parsed.hostname in {"yandex.ru", "yandex.com"}
        and parsed.username is None
        and parsed.password is None
        and re.fullmatch(r"/maps/user/[^/]+/?", parsed.path)
    ):
        return f"https://yandex.ru{parsed.path.rstrip('/')}"
    return value


# Формируем устойчивый ключ для объединения HTML и DOM одного отзыва.
def review_key(oid, row):
    # Полный текст обновляет тот же отзыв, сохраняя его ключ.
    # Предпочитаем публичный профиль; при его отсутствии используем отображаемое имя.
    author = canonical_profile_url(row["author_url"]) if row.get("author_url") else row.get("author")
    # Надпись интерфейса не является именем автора анонимного отзыва.
    if not row.get("author_url") and author == "Анонимный отзыв":
        # В HTML состояние такого автора обозначается null.
        author = None
    # Объединяем организацию, автора и дату для устойчивого ключа.
    identity = [oid, author, row.get("date")]
    # Проверяем наличие автора и даты для устойчивого ключа.
    if not row.get("author_url") or not identity[2]:
        # При нехватке автора или даты используем текст как дополнительный признак.
        text = row.get("text")
        # HTML и DOM могут различаться только пробелами по краям анонимного отзыва.
        identity.append(review_text(text) if isinstance(text, str) else text)
    # Сообщаем, что условие готовности или наличия проверки не выполнено.
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


# Переводим Retry-After из числа секунд или HTTP-даты в длительность ожидания.
def retry_seconds(value):
    # При отсутствии Retry-After дополнительное ожидание не задаётся.
    if not value:
        # Возвращаем неотрицательную длительность ожидания в секундах.
        return 0
    # Обрабатываем возможную ошибку следующего этапа.
    try:
        # Возвращаем неотрицательную длительность ожидания в секундах.
        seconds = float(value)
        # Неверный заголовок не должен создавать бесконечную задержку.
        return max(0, seconds) if math.isfinite(seconds) else 0
    # Перехватываем указанную ошибку и сохраняем понятный итог.
    except ValueError:
        # Обрабатываем возможную ошибку следующего этапа.
        try:
            # Возвращаем неотрицательную длительность ожидания в секундах.
            return max(
                # Не допускаем отрицательное время ожидания.
                0,
                (
                    # Сравниваем серверную HTTP-дату с текущим временем UTC.
                    parsedate_to_datetime(value)
                    # Вычитаем текущее время UTC из серверной даты.
                    - datetime.now(timezone.utc)
                    # Переводим разность времени в секунды.
                ).total_seconds(),
            )
        # Перехватываем указанную ошибку и сохраняем понятный итог.
        except (ValueError, TypeError):
            # Возвращаем неотрицательную длительность ожидания в секундах.
            return 0


# Проверяем совместимость, полноту полей и достаточность сохранённого результата.
# Проверяем структуру частичного или полного снимка отзывов.
def review_snapshot_valid(value):
    # Корень снимка и сведения об организации должны быть объектами.
    if not isinstance(value, dict) or not isinstance(value.get("company", {}), dict):
        # Неверный корень не передаётся функциям объединения.
        return False
    # Отзывы всегда хранятся в списке.
    reviews = value.get("reviews", [])
    # Каждый отзыв должен иметь поля, используемые при возобновлении.
    if not isinstance(reviews, list):
        # Не трактуем строку или объект как коллекцию отзывов.
        return False
    # Проверяем форму записей до обращения к ключам.
    for row in reviews:
        # Частично заполненные значения допускаются, неверные типы — нет.
        if (
            # Частично заполненные значения допускаются, неверные типы — нет.
            not isinstance(row, dict)
            # Частично заполненные значения допускаются, неверные типы — нет.
            or not isinstance(row.get("id"), str)
            # Частично заполненные значения допускаются, неверные типы — нет.
            or not row["id"]
            # Частично заполненные значения допускаются, неверные типы — нет.
            or "text" not in row
            # Частично заполненные значения допускаются, неверные типы — нет.
            or "rating" not in row
            # Частично заполненные значения допускаются, неверные типы — нет.
            or type(row.get("text_truncated")) is not bool
        ):
            # Сохраняем повреждённый оригинал и начинаем новый снимок.
            return False
        # Дату и сведения об авторе нельзя восстанавливать из вложенных объектов.
        if any(
            # Дату и сведения об авторе нельзя восстанавливать из вложенных объектов.
            row.get(key) is not None and not isinstance(row[key], str)
            # Дату и сведения об авторе нельзя восстанавливать из вложенных объектов.
            for key in ("date", "author", "author_url")
        ):
            # Повреждённый снимок сохраняется отдельно перед повторным сбором.
            return False
        # Текст может отсутствовать у ещё не прочитанного отзыва.
        if row["text"] is not None and not isinstance(row["text"], str):
            # Не принимаем вложенный объект за текст.
            return False
        # Оценка может отсутствовать, но не быть строкой или логическим значением.
        if row["rating"] is not None and (
            # Оценка может отсутствовать, но не быть строкой или логическим значением.
            type(row["rating"]) is not int
            # Оценка может отсутствовать, но не быть строкой или логическим значением.
            or row["rating"] not in range(1, 6)
        ):
            # Не исправляем чужие данные догадкой.
            return False
        # Новые признаки проверки необязательны для старых снимков, но имеют строгие типы.
        if "full_text_confirmed" in row and type(row["full_text_confirmed"]) is not bool:
            # Повреждённый признак не должен превращаться в готовый результат.
            return False
        # Принимаем только описанные источники подтверждения записи.
        if "observation" in row and (
            # Исключаем списки и другие неверные типы перед проверкой множества.
            not isinstance(row["observation"], str)
            # Источник подтверждения относится к последней попытке сбора.
            or row["observation"] not in {"dom", "html", "previous_snapshot"}
        ):
            # Не угадываем смысл неизвестного значения.
            return False
    # Повторяющиеся ключи не должны скрыто удаляться при восстановлении.
    if len({row["id"] for row in reviews}) != len(reviews):
        # Сохраняем весь повреждённый исходник перед новым сбором.
        return False
    # Счётчик сайта допускает отсутствие, но не неверный тип.
    count = value.get("company", {}).get("reviews_count")
    # Проверяем и диапазон счётчика, сохраняя нулевое число отзывов.
    return count is None or (type(count) is int and count >= 0)


# Проверяем заполненность полей независимо от происхождения сохранённой записи.
def review_fields_complete(row):
    # Отсутствие текста, даты или оценки не заменяем сведениями об организации.
    return (
        # Пустой текст не является содержательным отзывом.
        bool(row.get("text"))
        # Дата должна присутствовать и разбираться как ISO-дата.
        and bool(row.get("date"))
        # Ошибочная дата получает минимальное значение в общем преобразовании.
        and review_date(row) != datetime.min.replace(tzinfo=timezone.utc)
        # Логическое значение не является числовой оценкой.
        and type(row.get("rating")) is int
        # Индивидуальная оценка лежит от одного до пяти.
        and 1 <= row["rating"] <= 5
        # Полный текст проверяем по строгому логическому признаку.
        and row.get("text_truncated") is False
    )


# Проверяем полноту и совместимость сохранённых отзывов.
def cache_complete(cached, oid, limit):
    # Неверная структура означает необходимость восстановления, а не исключение.
    if not review_snapshot_valid(cached):
        # Не принимаем повреждённые данные за готовый результат.
        return False
    # Проверяем связанные условия перед продолжением обработки.
    if (
        # Проверяем идентификатор организации и версию формата.
        cached.get("organization_id") != oid
        # Проверяем идентификатор организации и версию формата.
        or cached.get("schema_version", 1) != SCHEMA_VERSION
    ):
        # Сообщаем, что условие готовности или наличия проверки не выполнено.
        return False
    # Подготавливаем список отзывов для обработки.
    reviews = cached.get("reviews", [])
    # Проверяем структуру и уникальность сохранённых отзывов.
    if not isinstance(reviews, list) or any(not isinstance(r, dict) for r in reviews):
        # Сообщаем, что условие готовности или наличия проверки не выполнено.
        return False
    # Проверяем структуру и уникальность сохранённых отзывов.
    if len({r.get("id") for r in reviews}) != len(reviews):
        # Сообщаем, что условие готовности или наличия проверки не выполнено.
        return False
    # Старые ключи сокращённого текста не должны скрывать одинаковые полные записи.
    if len({review_key(oid, r) for r in reviews}) != len(reviews):
        # Повторный сбор восстановит ключи без объявления дубликатов разными отзывами.
        return False
    # Новые снимки явно отличают прежние записи и непроверенные полные тексты.
    if any(
        # Совместимость старого готового формата сохраняется при отсутствии новых полей.
        ("full_text_confirmed" in row and row["full_text_confirmed"] is not True)
        # Запись только из прежнего снимка не подтверждает текущий готовый результат.
        or ("observation" in row and row["observation"] not in {"dom", "html"})
        # Проверяем каждую сохранённую запись.
        for row in reviews
    ):
        # Такой снимок требует повторного чтения источника.
        return False
    # Отклоняем кэш, если хотя бы у одного отзыва не хватает обязательного поля.
    # У ключа и полей записи одинаковые требования в кэше и текущем сборе.
    if any(not row.get("id") or not review_fields_complete(row) for row in reviews):
        # Незавершённая запись требует повторного чтения источника.
        return False
    # Проверяем принадлежность и совместимость сохранённого результата.
    if cached.get("status") == "complete":
        # Проверяем, достаточно ли сохранённых отзывов для указанного запроса.
        return len(reviews) == cached.get("company", {}).get("reviews_count")
    # Проверяем, достаточно ли сохранённых отзывов для указанного запроса.
    return (
        # Проверяем, достаточно ли сохранённых отзывов для указанного запроса.
        limit is not None
        # Проверяем, достаточно ли сохранённых отзывов для указанного запроса.
        and cached.get("status") == "capped"
        # Проверяем, достаточно ли сохранённых отзывов для указанного запроса.
        and cached.get("review_order") == "newest"
        # Проверяем, достаточно ли сохранённых отзывов для указанного запроса.
        and len(reviews) >= limit
    )


# Пересчитываем состав сохранённых записей для текущего результата или проекции кэша.
def update_completeness(result, oid, limit):
    # Число неподтверждённых прежних записей относится к итоговому файлу.
    result["unconfirmed_saved_reviews"] = sum(
        # Число неподтверждённых прежних записей относится к итоговому файлу.
        row.get("observation") == "previous_snapshot"
        # Число неподтверждённых прежних записей относится к итоговому файлу.
        for row in result["reviews"]
    )
    # Показываем, сколько сохранённых полных текстов проверено заново.
    result["confirmed_full_texts"] = sum(
        # Показываем, сколько сохранённых полных текстов проверено заново.
        row.get("full_text_confirmed") is True
        # Показываем, сколько сохранённых полных текстов проверено заново.
        for row in result["reviews"]
    )
    # Счётчик источника и сохранённый объём не следует смешивать с полнотой.
    declared = result["company"].get("reviews_count")
    # Сохраняем числовое объяснение состава частичного результата.
    result["completeness"] = {
        # Это число источника, а не число доступных для чтения DOM-карточек.
        "declared_reviews": declared,
        # Это фактический объём итогового файла с учётом лимита.
        "saved_reviews": len(result["reviews"]),
        # Эти сохранённые записи показаны в DOM при текущей попытке.
        "saved_from_dom": sum(row.get("observation") == "dom" for row in result["reviews"]),
        # Эти записи получены только из встроенного текущего HTML.
        "saved_from_html_only": sum(row.get("observation") == "html" for row in result["reviews"]),
        # Эти прежние записи не подтверждены текущей страницей.
        "retained_from_previous": result["unconfirmed_saved_reviews"],
        # Полноту текста проверяем отдельно от присутствия записи.
        "confirmed_full_texts": result["confirmed_full_texts"],
        # Это арифметическая разность; она не доказывает потерю записей парсером.
        "count_minus_dom": declared - result.get("observed_in_dom", 0)
        # Сохраняем числовое объяснение состава частичного результата.
        if declared is not None
        # Сохраняем числовое объяснение состава частичного результата.
        else None,
        # Прежние записи могут быть удалены или изменены на сайте.
        "count_minus_saved": declared - len(result["reviews"])
        # Сохраняем числовое объяснение состава частичного результата.
        if declared is not None
        # Сохраняем числовое объяснение состава частичного результата.
        else None,
        # Частичный снимок не должен становиться готовым кэшем.
        "ready_as_cache": result["status"] in {"complete", "capped"}
        # Сохраняем числовое объяснение состава частичного результата.
        and cache_complete(result, oid, limit),
    }


# Выбираем отдельный файл для каждого лимита, сохраняя полный снимок.
def review_target(folder, oid, limit):
    # Полный сбор использует прежнее имя; ограниченные выборки не затирают его.
    suffix = "" if limit is None else f".limit{limit}"
    # Возвращаем путь одного результата для CLI и географического поиска.
    return Path(folder) / f"{oid}{suffix}.json"


# Сортируем по дате отзыва, приводя разные записи ISO к одному часовому поясу.
def review_date(row):
    # Отсутствующая или неверная дата остаётся последней и не исправляется догадкой.
    try:
        # Поддерживаем как дату, так и ISO-время с часовым поясом.
        value = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
        # Для даты без часового пояса используем UTC.
        return (
            # Для даты без часового пояса используем UTC.
            value.replace(tzinfo=timezone.utc)
            # Для даты без часового пояса используем UTC.
            if value.tzinfo is None
            # Для даты без часового пояса используем UTC.
            else value.astimezone(timezone.utc)
        )
    # Некорректное значение не должно уничтожать остальные отзывы.
    except (KeyError, AttributeError, TypeError, ValueError):
        # Такие поля помечаются как неполные при проверке результата.
        return datetime.min.replace(tzinfo=timezone.utc)


# Используем готовый полный снимок для меньшего лимита без посещения сайта.
def load_review_cache(target, oid, limit):
    # Сначала проверяем собственный файл этой выборки.
    cached = read_snapshot(target, review_snapshot_valid)
    # Без полного снимка или при уже существующей выборке проекция не нужна.
    if cached or limit is None:
        # Незавершённый собственный снимок нужен для возобновления.
        return cached
    # Полный файл сохраняется отдельно и не изменяется при выборе меньшего объёма.
    full = read_snapshot(review_target(target.parent, oid, None), review_snapshot_valid)
    # Только готовый полный снимок подтверждает свежесть локальной выборки.
    if not cache_complete(full, oid, None):
        # При отсутствии полного кэша выполняем обычный сбор.
        return {}
    # Копируем верхний уровень, сохраняя исходный полный файл.
    cached = dict(full)
    # Выбираем самые свежие записи полного снимка.
    cached["reviews"] = sorted(full["reviews"], key=review_date, reverse=True)[:limit]
    # Отмечаем фактический объём и порядок локальной выборки.
    cached.update(limit=limit, review_order="newest", valid_reviews=len(cached["reviews"]))
    # При меньшем числе отзывов выборка также является полной.
    cached["status"] = "complete" if len(full["reviews"]) <= limit else "capped"
    # Показываем происхождение выборки для последующей диагностики.
    cached["cache_source"] = "complete_snapshot"
    # В старом формате этой диагностики нет; существующую пересчитываем для новой длины.
    if "completeness" in cached:
        # Сохраняем исходные наблюдения, но уточняем состав ограниченной выборки.
        update_completeness(cached, oid, limit)
    # Записываем отдельный файл, не открывая браузер.
    save_result(target, cached)
    # Передаём результат обычной проверке готовности.
    return cached


# Выбираем существующий порядок через публичное меню отзывов.
def select_review_order(page, label):
    # Используем кнопку именно в блоке отзывов, исключая другие меню страницы.
    button = page.locator(".business-reviews-card-view__ranking .rating-ranking-view")
    # Отсутствие доступного управления сортировкой является ошибкой выборки.
    button.wait_for(state="visible", timeout=15000)
    # Если страница уже выбрала нужный порядок, дополнительных действий не требуется.
    if button.inner_text().strip() == label:
        # Возвращаем подтверждённый интерфейсом порядок.
        return
    # Открываем меню обычным действием пользователя.
    button.click(timeout=10000)
    # Выбираем конкретную подпись только в меню сортировки отзывов.
    option = page.locator(".rating-ranking-view__popup-line")
    # Изменение атрибута доступности допускается, если видимая подпись остаётся точной.
    option.filter(has_text=re.compile(f"^{re.escape(label)}$")).click(timeout=10000)
    # Дожидаемся смены подписи выбранного порядка.
    page.wait_for_function(
        # Дожидаемся смены подписи выбранного порядка.
        "label => document.querySelector('.business-reviews-card-view__ranking .rating-ranking-view')?.textContent.trim() === label",
        # Передаём выбранную подпись без вставки текста в JavaScript.
        arg=label,
        # Ограничиваем ожидание результатов поиска пятнадцатью секундами.
        timeout=15000,
    )
    # Даём динамической выдаче завершить смену порядка до первого чтения.
    page.wait_for_timeout(1500)


# Подтверждаем новизну по меню и датам, в том числе при уже выбранной сортировке.
def select_newest(page):
    # Сам выбор использует только публичное управление страницей.
    select_review_order(page, "По новизне")
    # Проверяем даты первой порции: прежняя популярная выдача не должна пройти как свежая.
    page.wait_for_function(
        """() => {
      // Считываем даты только из текущих карточек отзывов.
      const dates = [...document.querySelectorAll('.business-reviews-card-view__review [itemprop="datePublished"]')].map(n => Date.parse(n.getAttribute('content')));
      // Не принимаем пустую выдачу или неподтверждённые даты.
      return dates.length > 0 && dates.every((v,i) => Number.isFinite(v) && (i === 0 || dates[i-1] >= v));
    }""",
        # Проверяем даты первой порции: прежняя популярная выдача не должна пройти как свежая.
        timeout=20000,
    )


# Распознаём страницу проверки пользователя по адресу и видимому тексту.
def challenge(page):
    # Ищем признак CAPTCHA в адресе открытой страницы.
    if re.search(r"/(?:showcaptcha|captcha)(?:/|$)", urlsplit(page.url).path, re.I):
        # Подтверждаем найденный признак проверки пользователя.
        return True
    # Обрабатываем возможную ошибку следующего этапа.
    try:
        # Получаем текст страницы для поиска признаков проверки пользователя.
        text = page.locator("body").inner_text(timeout=3000)[:4000]
    # Перехватываем указанную ошибку и сохраняем понятный итог.
    except BrowserError:
        # Сообщаем, что условие готовности или наличия проверки не выполнено.
        return False
    # Возвращаем признак обнаруженной проверки пользователя.
    return bool(
        # Ищем текстовый признак по регулярному выражению.
        re.search(
            # Ищем характерные сообщения о проверке пользователя или отказе в доступе.
            r"^\s*(?:подтвердите.{0,50}(?:не робот|человек)|вы не робот|access denied)",
            # Ищем признак проверки пользователя без учёта регистра.
            text,
            # Ищем признак проверки пользователя без учёта регистра.
            re.I,
        )
    )


# Собираем одну карточку, сохраняя прогресс и причину остановки.
def collect(page, oid, url, target, limit, scroll_pause):
    # Запоминаем монотонное время начала обработки карточки.
    started = time.monotonic()
    # Читаем сохранённый прогресс, если файл существует.
    previous = read_snapshot(target, review_snapshot_valid)
    # Проверяем принадлежность и совместимость сохранённого результата.
    if previous and (
        # Проверяем идентификатор организации и версию формата.
        previous.get("organization_id") != oid
        # Проверяем идентификатор организации и версию формата.
        or previous.get("schema_version", 1) != SCHEMA_VERSION
    ):
        # Сообщаем об ошибке входных или сохранённых данных.
        raise ValueError(
            # Поясняем причину несовместимости сохранённого файла.
            "Сохранённый файл относится к другой организации или версии формата"
        )
    # Храним отзывы по уникальному ключу; при завершении оставляем подтверждённые в текущем DOM.
    rows = {}
    # Восстанавливаем ключи по текущим полям: прежний ключ мог относиться к короткому тексту.
    for saved in previous.get("reviews", []):
        # Приводим полный текст HTML и DOM к одинаковой обработке внешних пробелов.
        if isinstance(saved.get("text"), str):
            # Слова, переносы внутри текста и знаки препинания сохраняются.
            saved = dict(saved, text=review_text(saved["text"]))
        # Пересчёт объединяет одинаковые полные версии прежних снимков.
        saved = dict(saved, id=review_key(oid, saved))
        # Сохраняем единственную версию с пересчитанным ключом.
        rows[saved["id"]] = saved
    # Учитываем отзывы, найденные в HTML или DOM при текущем запуске.
    observed = set()
    # Отдельно учитываем отзывы, действительно показанные текущей страницей.
    dom_observed = set()
    # Подтверждаем полный текст только после чтения его текущей HTML- или DOM-версии.
    full_text_observed = set()
    # Подготавливаем результат обработки и показатели полноты.
    result = {
        # Сохраняем версию формата JSON.
        "schema_version": SCHEMA_VERSION,
        # Сохраняем идентификатор организации.
        "organization_id": oid,
        # Сохраняем адрес страницы отзывов.
        "url": url,
        # Сохраняем состояние обработки.
        "status": "running",
        # Сохраняем время начала попытки в UTC.
        "collected_at": datetime.now(timezone.utc).isoformat(),
        # Сохраняем заданный предел числа отзывов.
        "limit": limit,
        # Сохраняем список отзывов.
        "reviews": list(rows.values()),
        # Сохраняем сведения об организации.
        "company": {
            # Счётчики и общую оценку подтверждаем заново после открытия страницы.
            key: value
            # Сохраняем прочие доступные сведения прежнего снимка.
            for key, value in previous.get("company", {}).items()
            # Прежний счётчик не определяет сортировку или полноту текущей попытки.
            if key not in {"reviews_count", "ratings_count", "rating"}
        },
        # Сохраняем счётчики запросов и HTTP-ошибки.
        "network": {
            # Сохраняем число браузерных запросов.
            "requests": 0,
            # Сохраняем число отменённых изображений, шрифтов и видео.
            "blocked_media": 0,
            # Сохраняем поле blocked_map_resources.
            "blocked_map_resources": 0,
            # Сохраняем обнаруженные HTTP-ошибки.
            "issues": [],
            # Сохраняем поле failures.
            "failures": [],
        },
        # Сохраняем число прокруток.
        "scrolls": 0,
        # Сохраняем число раскрытых текстов.
        "expanded_texts": 0,
    }
    # Храним причину остановки, обнаруженную при загрузке ресурсов.
    stopped = {}

    # Один наблюдатель следит за необходимыми загрузками текущей страницы.
    loads = PageLoads(page, result["network"])
    # Отказы первой динамической порции учитываются с начала открытия страницы.
    failures_since_progress = loads.marker()

    # Пропускаем необходимые ресурсы и отменяем загрузку изображений, шрифтов и медиа.
    def route_request(route):
        # Обновляем показатели загрузки ресурсов страницы.
        result["network"]["requests"] += 1
        # Исключаем ресурсы, не нужные для сбора текстов и оценок.
        if route.request.resource_type in {"image", "font", "media"}:
            # Обновляем показатели загрузки ресурсов страницы.
            result["network"]["blocked_media"] += 1
            # Отменяем загрузку ресурса, не нужного для текстов и оценок.
            route.abort()
        # Тайлы карты и значки соседних объектов не используются при чтении отзывов.
        elif urlsplit(route.request.url).hostname in {
            # Тайлы карты и значки соседних объектов не используются при чтении отзывов.
            "core-renderer-tiles.maps.yandex.ru",
            # Тайлы карты и значки соседних объектов не используются при чтении отзывов.
            "real-time-poi.maps.yandex.ru",
        }:
            # Отдельно учитываем отменённые данные фоновой карты.
            result["network"]["blocked_map_resources"] += 1
            # Содержимое отзывов и управление сортировкой остаются доступными.
            route.abort()
        # Обрабатываем альтернативный исход предыдущей проверки.
        else:
            # Разрешаем загрузку ресурса, необходимого странице.
            route.continue_()

    # Учитываем HTTP-ошибки, читая только адрес, код и заголовки ответа.
    def observe_response(response):
        # Проверяем код ответа перед продолжением загрузки.
        if response.status < 400:
            # Завершаем этот этап без дополнительных действий.
            return
        # Разбираем URL на схему, домен и путь.
        parsed = urlsplit(response.url)
        # Обновляем показатели загрузки ресурсов страницы.
        result["network"]["issues"].append(
            {
                # Сохраняем состояние обработки.
                "status": response.status,
                # Сохраняем домен ответа.
                "host": parsed.hostname,
                # Сохраняем путь ответа без параметров.
                "path": parsed.path,
                # Сохраняем тип ресурса.
                "resource_type": response.request.resource_type,
            }
        )
        # Читаем только HTTP-статус; содержимое служебных ответов не используем.
        # Проверяем допустимость домена, протокола или типа HTTP-ответа.
        if parsed.hostname in {
            # Проверяем основной домен Яндекса.
            "yandex.ru",
            # Проверяем международный домен Яндекса.
            "yandex.com",
            # Учитываем ограничения для HTML-документа и динамических загрузок страницы.
        } and response.request.resource_type in {"document", "xhr", "fetch"}:
            # Проверяем код ответа перед продолжением загрузки.
            if response.status == 429:
                # Запоминаем ограничение доступа для завершения текущей попытки.
                stopped.update(
                    # Задаём код ответа или причину остановки.
                    status="rate_limited",
                    # Сохраняем серверную рекомендацию о длительности ожидания.
                    retry_after=response.headers.get("retry-after"),
                )
            # Проверяем код ответа перед продолжением загрузки.
            elif response.status in {401, 403}:
                # Запоминаем ограничение доступа для завершения текущей попытки.
                stopped.update(status="access_denied")

    # Пересчитываем показатели полноты и записываем текущие отзывы на диск.
    def checkpoint():
        # Сохраняем число незавершённых загрузок вместе с данными.
        loads.checkpoint()
        # Обновляем список сохранённых отзывов.
        result["reviews"] = [
            # Отделяем наблюдение текущей записи от сохранённого полного текста.
            dict(
                # Сохраняем все содержательные поля отзыва.
                row,
                # Указываем, где запись подтверждена именно в этой попытке.
                observation="dom"
                # Обновляем список сохранённых отзывов.
                if row["id"] in dom_observed
                # Обновляем список сохранённых отзывов.
                else "html"
                # Обновляем список сохранённых отзывов.
                if row["id"] in observed
                # Обновляем список сохранённых отзывов.
                else "previous_snapshot",
                # Присутствие профиля и даты не подтверждает весь сокращённый текст.
                full_text_confirmed=row["id"] in full_text_observed,
            )
            # Ограничиваем только итоговую свежую выборку, а не диагностику DOM.
            for row in sorted(rows.values(), key=review_date, reverse=True)[:limit]
        ]
        # Измеряем время текущей попытки, включая ожидания.
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        # Считаем отзывы с текстом, датой, целой оценкой 1–5 и без сокращений.
        result["valid_reviews"] = sum(review_fields_complete(row) for row in result["reviews"])
        # Считаем отзывы без распознанной оценки.
        result["invalid_rating_count"] = sum(r["rating"] is None for r in result["reviews"])
        # Считаем тексты, которые остались сокращёнными.
        result["truncated_count"] = sum(r["text_truncated"] for r in result["reviews"])
        # Записываем число отзывов, найденных в этой попытке.
        result["observed_this_run"] = len(observed)
        # Записываем число отзывов, подтверждённых текущим DOM.
        result["observed_in_dom"] = len(dom_observed)
        # Показываем, сколько прежних записей ещё не подтверждено текущим запуском.
        result["unconfirmed_saved_reviews"] = sum(
            # Показываем, сколько прежних записей ещё не подтверждено текущим запуском.
            r["id"] not in observed
            # Показываем, сколько прежних записей ещё не подтверждено текущим запуском.
            for r in result["reviews"]
        )
        # Полнота относится к сохранённой выборке, а не размеру прежнего файла.
        update_completeness(result, oid, limit)
        # Сохраняем результат и журнал попыток на диск.
        save_result(target, result)

    # Обновляем отзыв по ключу, сохраняя уже полученный полный текст.
    def merge(row):
        # DOM убирает внешние пробелы; применяем ту же обработку к встроенному HTML.
        if isinstance(row.get("text"), str):
            # Не изменяем пробелы и переносы внутри текста.
            row["text"] = review_text(row["text"])
        # Дополняем отзыв идентификатором для объединения записей.
        row["id"] = review_key(oid, row)
        # Отмечаем отзыв как найденный в этой попытке.
        # Ищем ранее сохранённую версию этого отзыва.
        # При отсутствии публичного профиля короткий и полный текст имеют разные ключи.
        if not row.get("author_url") and row["id"] not in rows:
            # Сопоставляем только ту же отображаемую личность и дату, без объединения неоднозначных совпадений.
            def label(value):
                # Два отображения анонимного автора обозначают одну отсутствующую личность.
                return None if value in {None, "Анонимный отзыв"} else value

            # Убираем знак сокращения только у действительно свёрнутого текста.
            def snippet(value):
                # Признак сокращения определяет обработку завершающего многоточия.
                text = (value.get("text") or "").strip()
                # Полные тексты не изменяются при проверке совпадения.
                return text.rstrip(".…").strip() if value.get("text_truncated") else text

            # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
            matches = [
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                key
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                for key, old_row in rows.items()
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                if not old_row.get("author_url")
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                and label(old_row.get("author")) == label(row.get("author"))
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                and old_row.get("date") == row.get("date")
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                and bool(snippet(row))
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                and bool(snippet(old_row))
                # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                and (
                    (
                        # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                        row["text_truncated"]
                        # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                        and not old_row["text_truncated"]
                        # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                        and snippet(old_row).startswith(snippet(row))
                    )
                    # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                    or (
                        # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                        old_row["text_truncated"]
                        # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                        and not row["text_truncated"]
                        # Проверяем оба направления: HTML может содержать полный текст раньше DOM.
                        and snippet(row).startswith(snippet(old_row))
                    )
                )
            ]
            # Только единственное совпадение достаточно для сохранения общей идентичности.
            if len(matches) == 1:
                # Полный текст получает устойчивый ключ, независимый от прежнего сокращения.
                if not row["text_truncated"]:
                    # Удаляем короткую версию и её наблюдения перед сохранением полной.
                    old_key = matches[0]
                    # Переносим известный исходный идентификатор в раскрытый текст.
                    if rows[old_key].get("source_review_id"):
                        # Сохраняем исходную идентичность записи из HTML.
                        row["source_review_id"] = rows[old_key]["source_review_id"]
                    # Короткая версия больше не является отдельным отзывом.
                    del rows[old_key]
                    # Удаляем прежнее наблюдение короткой версии.
                    observed.discard(old_key)
                    # Счётчик DOM должен учитывать один раскрытый отзыв.
                    dom_observed.discard(old_key)
                    # Старый короткий ключ не подтверждает полный текст новой версии.
                    full_text_observed.discard(old_key)
                # Свёрнутый DOM использует ключ ранее сохранённого полного текста.
                else:
                    # Полный текст уже есть, его ключ сохраняется.
                    row["id"] = matches[0]
        # Отмечаем окончательный ключ текущего наблюдения.
        observed.add(row["id"])
        # Получаем ранее сохранённую версию того же отзыва.
        old = rows.get(row["id"])
        if old and old.get("author_url") and row.get("author_url") and old["author_url"] != row["author_url"]:
            result["profile_alias_merges"] = result.get("profile_alias_merges", 0) + 1
        # Запоминаем полноту реально прочитанной версии до использования прежнего текста.
        incoming_full_text = bool(row["text"]) and not row["text_truncated"]
        # Сохраняем полезные поля ранее полученной версии отзыва.
        if old and old["text"] and not old["text_truncated"] and row["text_truncated"]:
            # Полный текст подходит только к тому же сокращённому началу.
            prefix = (row["text"] or "").rstrip(".…").strip()
            # Изменённый текст требует повторного раскрытия; оценку читаем в любом случае.
            if not prefix or old["text"].startswith(prefix):
                # Сохраняем полный текст, обновляя остальные текущие поля отзыва.
                row["text"] = old["text"]
                # Свёрнутое отображение не делает сохранённый полный текст сокращённым.
                row["text_truncated"] = False
            # Изменённое начало отменяет подтверждение прежнего полного текста.
            else:
                diagnostic = result.setdefault(
                    "text_mismatch_observations", {"total": 0, "whitespace_prefix": 0}
                )
                diagnostic["total"] += 1
                diagnostic["whitespace_prefix"] += int(
                    " ".join(old["text"].split()).startswith(" ".join(prefix.split()))
                )
                # Новую версию нужно раскрыть обычной кнопкой страницы.
                full_text_observed.discard(row["id"])
        # Полный текст подтверждается только реально прочитанным содержимым.
        if incoming_full_text:
            # Повторное чтение той же записи не меняет число уникальных подтверждений.
            full_text_observed.add(row["id"])
        # Сохраняем полезные поля ранее полученной версии отзыва.
        if old and old.get("source_review_id"):
            # Дополняем отзыв идентификатором для объединения записей.
            row["source_review_id"] = old["source_review_id"]
        # Обновляем запись отзыва по устойчивому ключу.
        rows[row["id"]] = row

    # Объединяем порцию DOM и отдельно учитываем повторные физические карточки.
    def merge_dom(batch):
        # Используем пустую порцию только для отсутствующего DOM нулевой карточки.
        items = batch.get("reviews", [])
        # Подтверждаем каждую показанную запись с учётом раскрытия и смены ключа.
        for row in items:
            # Обновляем поля по той же схеме, что и при чтении HTML.
            merge(row)
            # Фиксируем присутствие записи в текущей попытке.
            dom_observed.add(row["id"])
        # Показываем физический объём последней порции без смешения с накопленным множеством.
        result["dom_cards_in_last_batch"] = len(items)
        # Короткий анонимный ключ может быть заменён полным в этой же порции.
        keys = {row["id"] for row in items if row["id"] in rows}
        # Повторные карточки не должны искусственно увеличивать число отзывов.
        result["duplicate_dom_cards_in_last_batch"] = len(items) - len(keys)

    # Подключаем обработчик загрузки ресурсов страницы.
    page.route("**/*", route_request)
    # Подключаем обработчик кодов HTTP-ответов.
    page.on("response", observe_response)
    # Сетевые отказы могут происходить без HTTP-кода.
    # Обрабатываем возможную ошибку следующего этапа.
    try:
        # Ограничиваем открытие страницы тремя попытками.
        for attempt in range(3):
            # Новое открытие после ограниченного технического повтора начинает новую попытку.
            if attempt:
                # Диагностика предыдущего отказа сохраняется в журнале.
                failures_since_progress = loads.marker()
            # Обрабатываем возможную ошибку следующего этапа.
            try:
                # Получаем ответ открытия страницы или его автономную подмену.
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                # Ограничение доступа имеет приоритет над повтором технической ошибки.
                if stopped:
                    # Сохраняем HTTP-диагностику и уже прочитанные записи.
                    result.update(stopped)
                    # Возврат через finally записывает снимок без лишнего запроса.
                    return result
                # Проверяем код ответа перед продолжением загрузки.
                if response and response.status in {502, 503, 504}:
                    # Не продолжаем открытие страницы после последней попытки.
                    if attempt == 2:
                        # Записываем причину завершения текущего этапа.
                        result["status"] = "server_error"
                        # Возвращаем результат; блок finally перед этим сохранит прогресс.
                        return result
                    # Выдерживаем паузу перед следующим обращением к сайту.
                    time.sleep(30 * (2**attempt))
                    # Переходим к следующей попытке или записи.
                    continue
                # Заканчиваем текущий цикл после полученного результата.
                break
            # Перехватываем указанную ошибку и сохраняем понятный итог.
            except (BrowserTimeout, BrowserError) as error:
                # Повторяем только таймаут или явный сетевой отказ открытия.
                if not isinstance(error, BrowserTimeout) and "net::ERR_" not in str(error):
                    # Программная ошибка браузера требует общей диагностики.
                    raise
                # Останавливаем обработку, если сайт требует проверку пользователя.
                if challenge(page) or stopped:
                    # Записываем причину завершения текущего этапа.
                    result["status"] = (
                        # Различаем проверку пользователя и техническую ошибку загрузки.
                        "captcha"
                        # Выбираем статус CAPTCHA при обнаружении проверки пользователя.
                        if challenge(page)
                        # Иначе используем обнаруженную причину ограничения доступа.
                        else stopped["status"]
                    )
                    # Возвращаем результат; блок finally перед этим сохранит прогресс.
                    return result
                # Не продолжаем открытие страницы после последней попытки.
                if attempt == 2:
                    # Записываем причину завершения текущего этапа.
                    result["status"] = (
                        # Записываем причину завершения текущего этапа.
                        "timeout"
                        # Записываем причину завершения текущего этапа.
                        if isinstance(error, BrowserTimeout)
                        # Записываем причину завершения текущего этапа.
                        else "network_error"
                    )
                    # Возвращаем результат; блок finally перед этим сохранит прогресс.
                    return result
                # Выдерживаем паузу перед следующим обращением к сайту.
                time.sleep(30 * (2**attempt))

        # Сохраняем адрес после возможного перенаправления.
        result["final_url"] = page.url
        # Проверяем, зарегистрировано ли ограничение доступа.
        if stopped:
            # Переносим обнаруженную причину остановки в результат.
            result.update(stopped)
            # Возвращаем результат; блок finally перед этим сохранит прогресс.
            return result
        # Останавливаем обработку, если сайт требует проверку пользователя.
        if challenge(page):
            # Записываем причину завершения текущего этапа.
            result["status"] = "captcha"
            # Возвращаем результат; блок finally перед этим сохранит прогресс.
            return result
        # Извлекаем встроенное состояние страницы; при его отсутствии используем DOM.
        # HTTP-ошибка не означает карточку без отзывов.
        if response and response.status >= 400:
            # Отсутствующую карточку отличаем от технической ошибки сайта.
            result["status"] = (
                # Отсутствующую карточку отличаем от технической ошибки сайта.
                "not_found"
                # Отсутствующую карточку отличаем от технической ошибки сайта.
                if response.status in {404, 410}
                # Отсутствующую карточку отличаем от технической ошибки сайта.
                else "server_error"
                # Отсутствующую карточку отличаем от технической ошибки сайта.
                if response.status >= 500
                # Отсутствующую карточку отличаем от технической ошибки сайта.
                else "http_error"
            )
            # Финальный блок сохраняет диагностику и прежние записи.
            return result
        # Подготавливаем чтение первой порции полных текстов из HTML.
        embedded = None
        # Обрабатываем возможную ошибку следующего этапа.
        try:
            # Извлекаем встроенное состояние страницы; при его отсутствии используем DOM.
            embedded = extract_saved_html(page.content(), oid)
            # Дополняем сведения об организации доступными значениями.
            result["company"].update(embedded["company"])
            # Некорректный тип счётчика HTML не должен участвовать в проверке полноты.
            for field in ("reviews_count", "ratings_count"):
                # Счётчики принимаем только как неотрицательные целые числа.
                if type(result["company"].get(field)) is not int or result["company"][field] < 0:
                    # Текущий DOM сможет подтвердить значение позже.
                    result["company"][field] = None
            # Последовательно обрабатываем записи указанного набора.
            for item in embedded["reviews"][:limit]:
                # Объединяем найденный отзыв с сохранёнными записями.
                merge(
                    {
                        # Сохраняем отображаемое имя автора.
                        "author": item["author"],
                        # Сохраняем публичную ссылку на профиль автора.
                        "author_url": f"https://yandex.ru/maps/user/{item['author_public_id']}"
                        # Строим ссылку на профиль только при наличии публичного идентификатора.
                        if item["author_public_id"]
                        # Указываем профиль автора только при наличии его публичного идентификатора.
                        else None,
                        # Сохраняем дату отзыва.
                        "date": item["date"],
                        # Сохраняем текст отзыва.
                        "text": item["text"],
                        # Сохраняем оценку отдельного отзыва.
                        "rating": item["rating"],
                        # Сохраняем признак сокращённого текста.
                        "text_truncated": False,
                        # Сохраняем исходный идентификатор отзыва из HTML.
                        "source_review_id": item["id"],
                    }
                )
            # Считаем тексты, полученные из встроенного HTML в пределах лимита.
            html_count = (
                # Считаем тексты, полученные из встроенного HTML в пределах лимита.
                embedded["loaded_reviews"]
                # Считаем тексты, полученные из встроенного HTML в пределах лимита.
                if limit is None
                # Считаем тексты, полученные из встроенного HTML в пределах лимита.
                else min(embedded["loaded_reviews"], limit)
            )
            # Записываем число текстов, извлечённых из HTML.
            result["html_full_texts"] = html_count
        # Перехватываем указанную ошибку и сохраняем понятный итог.
        except (ValueError, KeyError, StopIteration, TypeError, AttributeError):
            # Если формат встроенного JSON изменился, продолжаем чтение DOM.
            # Продолжаем чтение через DOM, если встроенный JSON недоступен.
            pass
        # Проверяем отсутствие отзывов по состоянию только что открытой страницы.
        if embedded is not None and embedded["company"].get("reviews_count") == 0:
            # Ждём текущий счётчик или видимые записи, не доверяя одному HTML-снимку.
            page.wait_for_function(
                # Проверяем публичный DOM и одну карточку при появлении списка.
                f"() => {{const batch=({EXTRACT})(1); return batch.reviews.length || batch.company.reviews_count !== null;}}",
                # Сохраняем обычный предел ожидания появления отзывов.
                timeout=20000,
                # Не сканируем страницу на каждом кадре анимации.
                polling=500,
            )
            # Проверяем DOM прежде, чем считать нулевой счётчик достоверным.
            current = page.evaluate(EXTRACT, limit) or {}
            # Сохраняем видимые отзывы, даже если встроенное состояние противоречиво.
            merge_dom(current)
            # Противоречивый счётчик не должен уничтожать уже прочитанные отзывы.
            if (
                # Любой видимый отзыв противоречит нулевому счётчику HTML.
                embedded["loaded_reviews"]
                # Противоречивый счётчик не должен уничтожать уже прочитанные отзывы.
                or current.get("reviews")
                # Ненулевой счётчик DOM тоже требует повторной проверки источника.
                or current.get("company", {}).get("reviews_count") not in {None, 0}
            ):
                # Сохраняем записи и предлагаем проверить страницу при повторном запуске.
                result["status"] = "count_mismatch"
                # Финальный блок запишет данные до возврата результата.
                return result
            # Храним отзывы по уникальному ключу; при завершении оставляем подтверждённые в текущем DOM.
            # Отсутствующий счётчик DOM не является подтверждённым нулём.
            if current.get("company", {}).get("reviews_count") != 0:
                # Сохраняем прежний прогресс вместо пустого готового файла.
                result["status"] = "timeout"
                # Поясняем, какое подтверждение не удалось получить.
                result["error"] = "Не удалось подтвердить отсутствие отзывов в DOM"
                # Следующий запуск повторит незавершённую проверку.
                return result
            # Только согласованный нулевой счётчик позволяет очистить старые записи.
            rows = {}
            # Записываем причину завершения текущего этапа.
            result["status"] = "complete"
            # Возвращаем результат; блок finally перед этим сохранит прогресс.
            return result
        # Ждём появления карточек отзывов.
        page.wait_for_selector(CARDS, timeout=20000)
        # Для больших карточек используем хронологическую выдачу также при полном сборе.
        # При недоступном HTML счётчик берём из текущего DOM до выбора сортировки.
        current = page.evaluate(EXTRACT, limit) or {}
        # Не заменяем подтверждённые значения отсутствующими полями.
        result["company"].update(
            # Не заменяем подтверждённые значения отсутствующими полями.
            {k: v for k, v in current.get("company", {}).items() if v is not None}
        )
        # Выбираем порядок по текущему, а не сохранённому числу отзывов.
        declared = result["company"].get("reviews_count")
        # Меню нужно только для ограниченной свежей выборки; полный набор сортируется локально.
        if limit is not None and (declared is None or declared > limit):
            # Неподтверждённая сортировка не должна давать успешный результат.
            try:
                # Меняем порядок через публичное меню страницы.
                select_newest(page)
            # Сохраняем уже прочитанные тексты, но не называем их свежей выборкой.
            except BrowserTimeout as error:
                # Явно указываем причину незавершённой выборки.
                result["status"] = "sort_unavailable"
                # Сохраняем краткую диагностику управления страницей.
                result["error"] = str(error).splitlines()[0]
                # Финальный блок сохранит данные и возможное ограничение доступа.
                return result
            # Свежесть частичной выборки подтверждена управлением страницы.
            result["review_order"] = "newest"

        # Считаем подряд идущие прокрутки без появления новых отзывов.
        stalls = 0
        # Первый просмотр страницы ещё не является неудачной попыткой подгрузки.
        baseline = None
        # Запоминаем видимые признаки, чтобы перестановка карточек не будила ожидание.
        browser_keys = set()
        # Читаем и прокручиваем до цели либо конечного числа ожиданий без прогресса.
        while True:
            # Останавливаем обработку, если сайт требует проверку пользователя.
            if challenge(page):
                # Записываем причину завершения текущего этапа.
                result["status"] = "captcha"
                # Заканчиваем текущий цикл после полученного результата.
                break
            # Сначала читаем DOM: найденные записи сохранятся и при последующем 429.
            # Читаем отзывы и счётчики из текущего DOM страницы.
            batch = page.evaluate(EXTRACT, limit)
            # Дополняем сведения об организации доступными значениями.
            result["company"].update(
                # Дополняем сведения только значениями, действительно найденными в DOM.
                {k: v for k, v in batch["company"].items() if v is not None}
            )
            # Объединяем записи, не принимая дубли карточек за новые отзывы.
            merge_dom(batch)
            # Сохраняем отзывы и показатели до следующего действия.
            checkpoint()
            # Проверяем, зарегистрировано ли ограничение доступа.
            if stopped:
                # Переносим обнаруженную причину остановки в результат.
                result.update(stopped)
                # Заканчиваем текущий цикл после полученного результата.
                break
            # Подтверждённая сортировка должна сохраняться при дальнейшей подгрузке.
            if (
                # Подтверждённая сортировка должна сохраняться при дальнейшей подгрузке.
                result.get("review_order") == "newest"
                # Подтверждённая сортировка должна сохраняться при дальнейшей подгрузке.
                and page.locator(
                    # Читаем публичную подпись текущего порядка.
                    ".business-reviews-card-view__ranking .rating-ranking-view"
                )
                # Подтверждённая сортировка должна сохраняться при дальнейшей подгрузке.
                .inner_text(timeout=10000)
                # Подтверждённая сортировка должна сохраняться при дальнейшей подгрузке.
                .strip()
                # Подтверждённая сортировка должна сохраняться при дальнейшей подгрузке.
                != "По новизне"
            ):
                # Не выдаём изменённый порядок за свежую выборку.
                result["status"] = "sort_unavailable"
                # Уже найденные записи будут сохранены.
                break
            # Передаём браузеру ключи уже полных текстов, чтобы не раскрывать их повторно.
            full_keys = [
                # Преобразуем данные в JSON с заданными параметрами записи.
                json.dumps(
                    # Сопоставляем отзыв по ссылке на автора и дате.
                    [r["author_url"], r["date"]],
                    # Сохраняем кириллицу без преобразования в последовательности Unicode.
                    ensure_ascii=False,
                    # Используем компактную запись JSON для сопоставления ключей с браузером.
                    separators=(",", ":"),
                )
                # Последовательно обрабатываем записи указанного набора.
                for r in rows.values()
                # Передаём ключи только тех отзывов, чьи тексты уже получены полностью.
                if r["text"]
                # Передаём браузеру ключи уже полных текстов, чтобы не раскрывать их повторно.
                and not r["text_truncated"]
                # Передаём браузеру ключи уже полных текстов, чтобы не раскрывать их повторно.
                and r.get("author_url")
                # Передаём браузеру ключи уже полных текстов, чтобы не раскрывать их повторно.
                and r["id"] in full_text_observed
            ]
            # Раскрываем только те сокращённые тексты, которых ещё нет полностью.
            expanded = page.evaluate(
                # Раскрываем недостающие тексты непосредственно в текущих DOM-карточках.
                r"""({limit, fullKeys}) => {
              // Считаем раскрытые тексты в этом проходе.
              let n=0;
              // Создаём множество ключей текстов, уже сохранённых полностью.
              const known = new Set(fullKeys);
              // Проверяем только карточки в пределах лимита.
              for (const card of [...document.querySelectorAll('.business-reviews-card-view__review')].slice(0,limit ?? undefined)) {
                // Сопоставляем автора и дату с уже сохранёнными текстами.
                const identity=JSON.stringify([
                  // Получаем ссылку на автора для сопоставления.
                  card.querySelector('.business-review-view__link')?.getAttribute('href') || null,
                  // Получаем дату отзыва для сопоставления.
                  card.querySelector('[itemprop="datePublished"]')?.getAttribute('content') || null,
                ]);
                // Пропускаем отзыв, полный текст которого уже сохранён.
                if (known.has(identity)) continue;
                // Ищем доступную кнопку раскрытия отзыва.
                const button=card.querySelector('.business-review-view__expand:not([aria-hidden="true"])');
                // Раскрываем свёрнутый текст и увеличиваем счётчик действий.
                if (button && card.querySelector('.spoiler-view__text._collapsed')) {button.click(); n++;}
              }
              // Возвращаем число раскрытых текстов.
              return n;
            }""",
                # Передаём лимит и ключи полных текстов в браузер.
                {"limit": limit, "fullKeys": full_keys},
            )
            # Учитываем раскрытые тексты.
            result["expanded_texts"] += expanded
            # Повторно читаем DOM после раскрытия текстов.
            if expanded:
                # Даём странице время завершить загрузку или раскрытие текста.
                page.wait_for_timeout(1000)
                # Читаем отзывы и счётчики из текущего DOM страницы.
                batch = page.evaluate(EXTRACT, limit)
                # Повторное чтение после раскрытия уточняет тексты и диагностику дублей.
                merge_dom(batch)
                # Сохраняем отзывы и показатели до следующего действия.
                checkpoint()
            # Читаем число отзывов, указанное на карточке организации.
            declared = result["company"].get("reviews_count")
            # Проверку готовности выполняем только после достижения счётчика или лимита.
            if (declared is not None and len(dom_observed) >= declared) or (
                # Проверку готовности выполняем только после достижения счётчика или лимита.
                limit is not None and len(dom_observed) >= limit
            ):
                # Не удаляем прежние записи до проверки текущего набора.
                current_rows = {key: row for key, row in rows.items() if key in dom_observed}
                # При лимите оцениваем только текущую свежую выборку.
                selected = sorted(current_rows.values(), key=review_date, reverse=True)[:limit]
                # Полный текст и обязательные поля подтверждаются текущей попыткой.
                ready = all(
                    # Полный текст и обязательные поля подтверждаются текущей попыткой.
                    review_fields_complete(row) and row["id"] in full_text_observed
                    # Полный текст и обязательные поля подтверждаются текущей попыткой.
                    for row in selected
                )
            # Сопоставляем уникальные текущие записи со счётчиком источника.
            if declared is not None and len(dom_observed) >= declared:
                # Противоречивый счётчик не разрешает удалять прежние записи.
                if len(dom_observed) > declared:
                    # Все текущие и прежние данные остаются в незавершённом снимке.
                    result["status"] = "count_mismatch"
                # Только подтверждённый готовый состав заменяет прежний снимок.
                elif ready:
                    # Исключаем прежние записи, отсутствующие в проверенном текущем наборе.
                    rows = current_rows
                    # Полный готовый набор локально упорядочен по датам.
                    result["review_order"] = "newest"
                    # Счётчик и обязательные текущие поля согласованы.
                    result["status"] = "complete"
                # Совпадение числа записей не заменяет проверку содержимого.
                else:
                    # Сохраняем прежний прогресс до подтверждения недостающих полей.
                    result["status"] = "incomplete_fields"
                # Финальный блок сохранит выбранный состав без промежуточного удаления.
                break
            # Лимит подтверждается текущими записями с выбранной сортировкой.
            if limit is not None and len(dom_observed) >= limit:
                # Успешная выборка не должна содержать неподтверждённые прежние записи.
                if ready:
                    # Оставляем текущие записи; ограничение применяется при сохранении.
                    rows = current_rows
                    # Подтверждённый порядок либо полный малый набор дают свежую выборку.
                    result["review_order"] = "newest"
                    # Цель ограниченного сбора достигнута.
                    result["status"] = "capped"
                # Неполные поля не позволяют заменить прежний результат готовой выборкой.
                else:
                    # Старые записи остаются явно помеченными в незавершённом файле.
                    result["status"] = "incomplete_fields"
                # Финальный блок сохранит итоговый состав и его происхождение.
                break
            # События доступа могли прийти во время раскрытия текста.
            if stopped:
                # Ограничение имеет приоритет перед продолжением загрузки.
                result.update(stopped)
                # Не создаём следующую попытку.
                break
            # После ожидания сначала читаем DOM, затем решаем, был ли прогресс.
            if baseline is not None:
                # Только увеличение числа уникальных отзывов сбрасывает счётчик.
                if len(dom_observed) > baseline:
                    # Новые записи подтверждены извлечением и объединением.
                    stalls = 0
                    # Старые сетевые отказы не относятся к следующей подгрузке.
                    failures_since_progress = loads.marker()
                # Перестановка карточек и HTTP 200 без записей не являются прогрессом.
                else:
                    # Учитываем одну завершённую попытку ожидания.
                    stalls += 1
                # Число допустимых неудачных ожиданий сохраняется прежним.
                if stalls >= 3:
                    # Отличаем пустую выдачу, сетевой отказ и ещё выполняющуюся загрузку.
                    result["status"] = loads.stalled_status(failures_since_progress, "stalled")
                    # Сохраняем основания остановки без предположения о полноте.
                    result["stall_diagnostics"] = {
                        # Показываем текущий счётчик источника.
                        "declared_reviews": declared,
                        # Показываем число сохранённых уникальных отзывов.
                        "saved_unique_reviews": len(rows),
                        # Показываем число подтверждённых DOM-записей.
                        "confirmed_in_dom": len(dom_observed),
                        # Показываем число ожиданий без новых записей.
                        "attempts_without_progress": stalls,
                    }
                    # Последняя порция уже прочитана и сохранена.
                    break
                # Не запускаем повторную прокрутку поверх незавершённой загрузки.
                if stalls and not loads.pending:
                    # Повторно вызываем штатное появление индикатора в окне.
                    page.evaluate(SCROLL_BACK, CARDS)
                    # Сохраняем прежнюю паузу восстановления наблюдения видимости.
                    page.wait_for_timeout(500)
            # Запоминаем число уникальных записей до следующего ожидания.
            baseline = len(dom_observed)
            # Дополняем признаки публичных карточек; число DOM-узлов может не расти.
            browser_keys.update(page.evaluate(DOM_KEYS, CARDS) or [])
            # Продолжающуюся загрузку ждём на месте, без повторной прокрутки.
            if not loads.pending:
                # Прокручиваем публичный список и его индикатор следующей порции.
                page.evaluate(
                    # Находим последнюю карточку и прокручиваем её панель одним действием.
                    r"""selector => {
                  // Заново находим последнюю карточку, не сохраняя устаревающий DOM-указатель.
                  const last = [...document.querySelectorAll(selector)].at(-1);
                  // Если карточек нет, прокрутка не требуется.
                  if (!last) return;
                  // Индикатор подгрузки может находиться ниже последнего отзыва и оставаться вне окна.
                  const loading=[...last.closest('.business-reviews-card-view')?.querySelectorAll('.business-reviews-card-view__skeleton') || []].at(-1);
                  // Подводим индикатор подгрузки либо последнюю карточку к нижней границе окна.
                  (loading || last).scrollIntoView({block:'end'});
                  // Поднимаемся по родительским элементам в поиске прокручиваемой панели.
                  for (let parent=last.parentElement; parent; parent=parent.parentElement) {
                    // Выбираем панель, содержимое которой действительно можно прокрутить.
                    if (/auto|scroll/.test(getComputedStyle(parent).overflowY) && parent.scrollHeight > parent.clientHeight) {
                      // Прокручиваем найденную панель до конца.
                      parent.scrollTop=parent.scrollHeight;
                      // Заканчиваем поиск после прокрутки нужной панели.
                      break;
                    }
                  }
                }""",
                    # Передаём селектор текущих карточек в браузер.
                    CARDS,
                )
                # Учитываем выполненную прокрутку.
                result["scrolls"] += 1
                # Даём странице время завершить загрузку или раскрытие текста.
            page.wait_for_timeout(int(scroll_pause * 1000))
            # Обрабатываем возможную ошибку следующего этапа.
            try:
                # Уведомление ищет новую карточку в любом месте, с редким опросом DOM.
                page.wait_for_function(
                    # Перестановка известных записей не завершает ожидание.
                    DOM_PROGRESS,
                    # Передаём признаки только уже виденных публичных карточек.
                    arg={"selector": CARDS, "known": list(browser_keys)},
                    # Сохраняем прежний предел ожидания одного прохода.
                    timeout=10000,
                    # Не сканируем длинный список на каждом кадре анимации.
                    polling=500,
                )
            # Истечение ожидания само по себе не означает конец списка.
            except BrowserTimeout:
                # Следующий проход обязательно извлечёт DOM перед решением об остановке.
                pass
    # Обрабатываем возможную ошибку следующего этапа.
    except KeyboardInterrupt:
        # Финальный блок сохранит уже полученные записи.
        result["status"] = "interrupted"
        # Верхний уровень завершает очередь без новых запросов.
        raise
    # Обрабатываем возможную ошибку следующего этапа.
    except BrowserTimeout as error:
        # Записываем причину завершения текущего этапа.
        result["status"] = (
            # Различаем проверку пользователя и техническую ошибку загрузки.
            "captcha"
            # Выбираем статус CAPTCHA при обнаружении проверки пользователя.
            if challenge(page)
            # При отсутствии ограничения доступа отмечаем таймаут ожидания.
            else (stopped.get("status") or "timeout")
        )
        # Сохраняем описание ошибки для диагностики.
        result["error"] = str(error).splitlines()[0]
    # Перехватываем указанную ошибку и сохраняем понятный итог.
    except Exception as error:
        # Записываем причину завершения текущего этапа.
        result["status"] = stopped.get("status") or (
            # Записываем причину завершения текущего этапа.
            "browser_closed"
            # Записываем причину завершения текущего этапа.
            if getattr(page, "is_closed", lambda: False)()
            # Записываем причину завершения текущего этапа.
            else "error"
        )
        # Сохраняем описание ошибки для диагностики.
        result["error"] = f"{type(error).__name__}: {error}"
    # Выполняем сохранение или освобождение ресурсов при любом исходе.
    finally:
        # Проверяем, зарегистрировано ли ограничение доступа.
        if stopped:
            # Переносим обнаруженную причину остановки в результат.
            result.update(stopped)
        # Ошибка записи тоже должна освобождать обработчики текущей карточки.
        try:
            # Атомарная запись сохраняет прежний файл при отказе диска.
            checkpoint()
        # Освобождаем подписки даже при невозможности сохранить результат.
        finally:
            # Отказы следующей карточки должны попадать в её собственный журнал.
            loads.close()
            # Удаляем обработчик ответов перед следующей организацией.
            page.remove_listener("response", observe_response)
            # Удаляем обработчик ресурсов перед следующей организацией.
            page.unroute("**/*", route_request)
    # Возвращаем результат; блок finally перед этим сохранит прогресс.
    return result


# Выполняем последовательную очередь с сохранением её текущего состояния.
def collect_many(
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    page,
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    jobs,
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    limit,
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    scroll_pause,
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    pause,
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    rate_retries,
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    user_agent,
    # Выполняем последовательную очередь с сохранением её текущего состояния.
    progress_target=None,
):
    # Накапливаем только компактные итоги отдельных карточек.
    outcomes = []

    # Сохраняем состояние очереди после каждой карточки и при прерывании.
    def progress(status):
        # Отдельный файл используется только при явно заданном пути.
        if progress_target is not None:
            # Прерванная карточка остаётся среди незавершённых заданий.
            processed = {
                # Прерванная карточка остаётся среди незавершённых заданий.
                row["organization_id"]
                # Прерванная карточка остаётся среди незавершённых заданий.
                for row in outcomes
                # Прерванная карточка остаётся среди незавершённых заданий.
                if row["status"] != "interrupted"
            }
            # Записываем состояние независимо от итогов прошлых запусков.
            save_result(
                # Записываем состояние независимо от итогов прошлых запусков.
                progress_target,
                {
                    # Сохраняем состояние поиска.
                    "status": status,
                    # Сохраняем итоги фактически обработанных карточек.
                    "outcomes": outcomes,
                    # Сохраняем число карточек в новой очереди.
                    "requested_jobs": len(jobs),
                    # Сохраняем поле remaining_ids.
                    "remaining_ids": [oid for oid, _, _ in jobs if oid not in processed],
                    # Отделяем не начатые карточки от обработанных, но незавершённых.
                    "retry_ids": [
                        # Записываем состояние независимо от итогов прошлых запусков.
                        row["organization_id"]
                        # Записываем состояние независимо от итогов прошлых запусков.
                        for row in outcomes
                        # Записываем состояние независимо от итогов прошлых запусков.
                        if row["status"] not in {"complete", "capped"}
                    ],
                },
            )

    # Новый запуск не должен показывать старый отчёт как текущий.
    progress("running")
    # Отдельно отмечаем текущую карточку при пользовательском прерывании.
    current = None
    # Любое прерывание должно сохранить положение очереди.
    try:
        # Обрабатываем заданный порядок, не создавая дополнительных браузеров.
        for index, (oid, url, target) in enumerate(jobs):
            # Межкарточная пауза остаётся независимой от задержки после 429.
            if index:
                # Соблюдаем выбранную пользователем частоту обращений.
                time.sleep(pause)
            # Запоминаем открываемую карточку для отчёта о прерывании.
            current = (oid, target)
            # Показываем начало очередного задания.
            print(
                # Показываем начало очередного задания.
                json.dumps({"organization_id": oid, "event": "open"}, ensure_ascii=False),
                # Сразу показываем состояние задачи в терминале.
                flush=True,
            )
            # Храним историю повторов текущего запуска.
            attempts = []
            # Число повторов ограничено проверенными параметрами CLI.
            for retry in range(rate_retries + 1):
                # Получаем и сохраняем данные одной карточки.
                result = collect(page, oid, url, target, limit, scroll_pause)
                # Фиксируем фактический User-Agent браузера.
                result["user_agent"] = user_agent
                # Сохраняем статус и HTTP-ошибки каждой попытки.
                attempts.append(
                    {
                        # Сохраняем поле attempt.
                        "attempt": retry + 1,
                        # Сохраняем поле time_utc.
                        "time_utc": result["collected_at"],
                        # Сохраняем состояние поиска.
                        "status": result["status"],
                        # Сохраняем число отзывов с полными полями.
                        "valid_reviews": result["valid_reviews"],
                        # Сохраняем поле http_issues.
                        "http_issues": result["network"]["issues"],
                    }
                )
                # Не заменяем историю текущего запуска единственной последней попыткой.
                result["attempts"] = attempts.copy()
                # Записываем итог с диагностикой перед ожиданием или следующим заданием.
                save_result(target, result)
                # Повторы здесь относятся только к 429.
                if result["status"] != "rate_limited" or retry == rate_retries:
                    # Другие состояния передаются уровню обработки очереди.
                    break
                # Учитываем как собственную задержку, так и рекомендацию сервера.
                cooldown = max(60 * 2**retry, retry_seconds(result.get("retry_after")))
                # Чрезмерная рекомендация сервера прекращает текущую очередь.
                if cooldown > 900:
                    # Последний 429 останется в результате.
                    break
                # Показываем ожидаемую паузу без нового запроса.
                print(
                    # Показываем ожидаемую паузу без нового запроса.
                    json.dumps(
                        {
                            # Сохраняем поле event.
                            "event": "cooldown",
                            # Сохраняем идентификатор организации Яндекса.
                            "organization_id": oid,
                            # Сохраняем поле seconds.
                            "seconds": cooldown,
                            # Сохраняем поле retry.
                            "retry": retry + 1,
                        }
                    ),
                    # Сразу показываем состояние задачи в терминале.
                    flush=True,
                )
                # Ожидаем ограниченное время перед повтором.
                time.sleep(cooldown)
            # Показываем проверяемые показатели последней попытки.
            print(
                # Показываем проверяемые показатели последней попытки.
                json.dumps(
                    {
                        # Показываем проверяемые показатели последней попытки.
                        key: result[key]
                        # Показываем проверяемые показатели последней попытки.
                        for key in [
                            # Показываем проверяемые показатели последней попытки.
                            "organization_id",
                            # Показываем проверяемые показатели последней попытки.
                            "status",
                            # Показываем проверяемые показатели последней попытки.
                            "valid_reviews",
                            # Показываем проверяемые показатели последней попытки.
                            "truncated_count",
                            # Показываем проверяемые показатели последней попытки.
                            "elapsed_seconds",
                            # Показываем проверяемые показатели последней попытки.
                            "scrolls",
                            # Показываем проверяемые показатели последней попытки.
                            "expanded_texts",
                        ]
                    },
                    # Сохраняем русские названия в JSON без Unicode-последовательностей.
                    ensure_ascii=False,
                ),
                # Сразу показываем состояние задачи в терминале.
                flush=True,
            )
            # Добавляем фактически обработанную карточку в журнал очереди.
            outcomes.append(
                {
                    # Сохраняем идентификатор организации Яндекса.
                    "organization_id": oid,
                    # Сохраняем состояние поиска.
                    "status": result["status"],
                    # Сохраняем число отзывов с полными полями.
                    "valid_reviews": result["valid_reviews"],
                }
            )
            # Карточка уже записана и не считается текущим прерванным заданием.
            current = None
            # Сохраняем промежуточный журнал до открытия следующей страницы.
            progress("running")
            # Ограничение доступа или закрытый браузер прекращают дальнейшие запросы.
            if result["status"] in {
                # Ограничение доступа или закрытый браузер прекращают дальнейшие запросы.
                "captcha",
                # Ограничение доступа или закрытый браузер прекращают дальнейшие запросы.
                "rate_limited",
                # Ограничение доступа или закрытый браузер прекращают дальнейшие запросы.
                "access_denied",
                # Ограничение доступа или закрытый браузер прекращают дальнейшие запросы.
                "browser_closed",
                # Ограничение доступа или закрытый браузер прекращают дальнейшие запросы.
                "network_error",
                # Серверная ошибка или зависшая загрузка требуют повторного запуска.
                "server_error",
                # Не продолжаем очередь поверх незавершённой необходимой загрузки.
                "load_timeout",
            }:
                # Сохраняем оставшиеся идентификаторы для следующего запуска.
                progress("stopped")
                # Не повторяем запросы к другим карточкам после блокировки.
                return outcomes
        # Завершение очереди не означает полноту всех отзывов: проверяются статусы карточек.
        progress("finished")
    # Пользовательское прерывание сохраняет журнал без новых запросов.
    except KeyboardInterrupt:
        # Если прерывание произошло внутри карточки, читаем уже сохранённый прогресс.
        if current is not None:
            # Получаем текущий идентификатор и путь снимка.
            oid, target = current
            # Снимок может отсутствовать при прерывании до первого сохранения.
            snapshot = read_snapshot(target, review_snapshot_valid)
            # Отмечаем карточку как незавершённую даже при наличии прежних данных.
            outcomes.append(
                {
                    # Сохраняем идентификатор организации Яндекса.
                    "organization_id": oid,
                    # Сохраняем состояние поиска.
                    "status": "interrupted",
                    # Сохраняем число отзывов с полными полями.
                    "valid_reviews": snapshot.get("valid_reviews", 0),
                }
            )
        # Файл очереди должен отражать фактический конец этого запуска.
        progress("interrupted")
        # Верхний уровень завершает процесс стандартным кодом прерывания.
        raise
    # Ошибка диска или запуска также не означает успешно пройденную очередь.
    except Exception:
        # Сохраняем доступные компактные итоги, если диск ещё доступен.
        progress("error")
        # Сохраняем причину исходной ошибки для верхнего уровня.
        raise
    # Возвращаем совместимый список обработанных карточек.
    return outcomes


# Разбираем параметры запуска и последовательно обрабатываем организации.
def main():
    # Создаём разборщик HTML или параметров командной строки.
    parser = argparse.ArgumentParser(description=__doc__)
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Принимаем одну или несколько ссылок либо числовых идентификаторов.
        "urls",
        # Разрешаем передать одну или несколько организаций.
        nargs="+",
        # Показываем назначение параметра в справке запуска.
        help="Ссылки на карточки организаций или их числовые идентификаторы",
    )
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Задаём имя параметра командной строки.
        "--limit",
        # Допускаем общее имя параметра для обоих способов запуска.
        "--max-reviews",
        # Преобразуем ввод в требуемый тип данных.
        type=int,
        # Задаём значение параметра по умолчанию.
        default=None,
        # Показываем назначение параметра в справке запуска.
        help="Максимум самых свежих отзывов на организацию; без параметра собираем все",
    )
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Задаём имя параметра командной строки.
        "--output",
        # Преобразуем ввод в требуемый тип данных.
        type=Path,
        # Задаём значение параметра по умолчанию.
        default=Path("review_results"),
        # Показываем назначение параметра в справке запуска.
        help="Папка с результатами и сохранённым прогрессом",
    )
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Задаём имя параметра командной строки.
        "--pause",
        # Преобразуем ввод в требуемый тип данных.
        type=float,
        # Задаём значение параметра по умолчанию.
        default=20,
        # Показываем назначение параметра в справке запуска.
        help="Пауза между карточками в секундах: не менее 10",
    )
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Задаём имя параметра командной строки.
        "--scroll-pause",
        # Преобразуем ввод в требуемый тип данных.
        type=float,
        # Задаём значение параметра по умолчанию.
        default=5,
        # Показываем назначение параметра в справке запуска.
        help="Пауза после прокрутки в секундах: не менее 3",
    )
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Задаём имя параметра командной строки.
        "--429-retries",
        # Задаём имя параметра в разобранных аргументах.
        dest="rate_retries",
        # Преобразуем ввод в требуемый тип данных.
        type=int,
        # Задаём значение параметра по умолчанию.
        default=3,
        # Показываем назначение параметра в справке запуска.
        help="Число повторов после HTTP 429: 0–3",
    )
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Задаём имя параметра командной строки.
        "--executable",
        # Описываем необязательный путь к установленному браузеру.
        help="Путь к исполняемому файлу установленного Chromium",
    )
    # Добавляем параметр командной строки с описанием.
    parser.add_argument(
        # Задаём имя параметра командной строки.
        "--refresh",
        # Обрабатываем параметр как переключатель.
        action="store_true",
        # Показываем назначение параметра в справке запуска.
        help="Повторно посетить карточки с уже завершённым результатом",
    )
    # Получаем значения параметров командной строки.
    args = parser.parse_args()
    # Проверяем связанные условия перед продолжением обработки.
    if (
        # Проверяем разрешённый диапазон параметра запуска.
        (args.limit is not None and args.limit < 1)
        # Проверяем разрешённый диапазон параметра запуска.
        or not math.isfinite(args.pause)
        # Межстраничная пауза должна быть конечной и не слишком короткой.
        or args.pause < 10
        # Проверяем разрешённый диапазон параметра запуска.
        or not math.isfinite(args.scroll_pause)
        # Пауза прокрутки должна быть конечной и не слишком короткой.
        or args.scroll_pause < 3
        # Проверяем разрешённый диапазон параметра запуска.
        or not 0 <= args.rate_retries <= 3
    ):
        # Завершаем запуск с понятным описанием недопустимого параметра.
        parser.error(
            # Показываем допустимые диапазоны всех проверяемых параметров.
            "Требуется: limit >= 1 либо без лимита; pause >= 10; scroll-pause >= 3; 429-retries 0–3"
        )
    # Обрабатываем возможную ошибку следующего этапа.
    try:
        # Проверяем адреса и исключаем повторяющиеся страницы отзывов.
        urls = list(dict.fromkeys(canonical_url(url) for url in args.urls))
    # Перехватываем указанную ошибку и сохраняем понятный итог.
    except ValueError as error:
        # Завершаем запуск с понятным описанием недопустимого параметра.
        parser.error(str(error))
    # Одна папка результатов принадлежит только одной активной очереди.
    with output_lock(args.output):
        # Создаём папку результатов, если она ещё отсутствует.
        args.output.mkdir(parents=True, exist_ok=True)
        # Формируем очередь только из организаций, требующих обращения к сайту.
        pending = []
        # Проверяем сохранённый результат каждой организации до запуска браузера.
        for oid, url in urls:
            # Выбираем JSON-файл результатов одной организации.
            target = review_target(args.output, oid, args.limit)
            # Читаем готовый результат для проверки возможности повторного использования.
            cached = load_review_cache(target, oid, args.limit)
            # Используем готовый кэш, если обновление не запрошено.
            if not args.refresh and cache_complete(cached, oid, args.limit):
                # Выводим краткое событие или результат в терминал.
                print(
                    # Преобразуем данные в JSON с заданными параметрами записи.
                    json.dumps(
                        {
                            # Сохраняем идентификатор организации.
                            "organization_id": oid,
                            # Сохраняем состояние обработки.
                            "status": "cache_hit",
                            # Сохраняем число отзывов с полными обязательными полями.
                            "valid_reviews": len(cached["reviews"]),
                        },
                        # Сохраняем кириллицу без преобразования в последовательности Unicode.
                        ensure_ascii=False,
                    ),
                    # Сразу показываем событие в терминале.
                    flush=True,
                )
            # Обрабатываем альтернативный исход предыдущей проверки.
            else:
                # Добавляем организацию в очередь обращений к сайту.
                pending.append((oid, url, target))
        # Если очередь пуста, браузер запускать не требуется.
        if not pending:
            # Завершаем этот этап без дополнительных действий.
            return
        # Открываем Playwright с автоматическим освобождением ресурсов.
        with sync_playwright() as playwright:
            # При отсутствии устанавливаем совместимый Chromium и открываем браузер.
            browser = launch_chromium(playwright, args.executable)
            # Обрабатываем возможную ошибку следующего этапа.
            try:
                # Открываем временную вкладку для определения версии браузера.
                probe = browser.new_page()
                # Читаем исходную строку User-Agent установленного Chromium.
                native_agent = probe.evaluate("navigator.userAgent")
                # Закрываем временную вкладку после чтения User-Agent.
                probe.close()
                # Используем обозначение Chrome с фактической версией браузера.
                user_agent = native_agent.replace("HeadlessChrome/", "Chrome/")
                # Создаём общую браузерную сессию с русской локалью и одним набором cookies.
                context = browser.new_context(
                    # Используем русскую локаль страницы.
                    locale="ru-RU",
                    # Задаём размер окна для стабильной прокрутки панели.
                    viewport={"width": 1366, "height": 900},
                    # Используем обозначение Chrome с фактической версией браузера.
                    user_agent=user_agent,
                )
                # Подготавливаем страницу для чтения DOM или автономную подмену браузера.
                page = context.new_page()
                # Передаём очередь общему обработчику отзывов в текущей сессии.
                outcomes = collect_many(
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    page,
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    pending,
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    args.limit,
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    args.scroll_pause,
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    args.pause,
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    args.rate_retries,
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    user_agent,
                    # Передаём очередь общему обработчику отзывов в текущей сессии.
                    args.output / "review_jobs.json",
                )
                # Частичный или остановленный сбор не должен возвращать код успешного выпуска.
                if any(item["status"] not in {"complete", "capped"} for item in outcomes):
                    # JSON сохраняет подробную причину для каждой карточки.
                    raise SystemExit(1)
            # Выполняем сохранение или освобождение ресурсов при любом исходе.
            finally:
                # Закрываем браузер даже при ошибке обработки.
                browser.close()


# Завершаем запуск понятным сообщением, сохраняя исходную причину сбоя.
def run_cli(entry):
    # Ошибки параметров остаются штатными ошибками argparse.
    try:
        # Выполняем поиск либо очередь карточек.
        entry()
    # Прерывание уже сохранено внутренними контрольными точками.
    except KeyboardInterrupt:
        # Не обещаем сохранение ещё не прочитанных данных.
        print(
            # Не обещаем сохранение ещё не прочитанных данных.
            "Запуск прерван. Уже записанные снимки сохранены; повторите ту же команду для продолжения.",
            # Не обещаем сохранение ещё не прочитанных данных.
            file=sys.stderr,
        )
        # Возвращаем стандартный код пользовательского прерывания.
        raise SystemExit(130)
    # Известные ошибки доступа к файлам и браузеру не требуют длинной трассировки.
    except (OSError, ValueError, RuntimeError, BrowserError) as error:
        # Показываем тип и сообщение; исключение не считается успешным результатом.
        print(
            # Показываем тип и сообщение; исключение не считается успешным результатом.
            f"Запуск не завершён: {type(error).__name__}: {str(error).splitlines()[0]}",
            # Показываем тип и сообщение; исключение не считается успешным результатом.
            file=sys.stderr,
        )
        # Возвращаем ненулевой код для автоматической проверки запуска.
        raise SystemExit(1)


# Выполняем запуск только при прямом вызове файла.
if __name__ == "__main__":
    # Запускаем обработку параметров и очередь организаций.
    run_cli(main)
