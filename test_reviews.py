"""Проверка извлечения, сохранения и остановки без запросов к Яндекс Картам."""

# Подключаем чтение и запись JSON.
import json

# Разбираем вымышленный HTML для проверки анонимных отзывов.
import re

# Подключаем чтение переменных окружения.
import os

# Подключаем автономные проверки.
import unittest

# Подключаем работу с путями и файлами.
from pathlib import Path

# Подключаем временные папки для проверок.
from tempfile import TemporaryDirectory

# Подключаем подмену объектов браузера.
from types import SimpleNamespace

# Подключаем подмену вызовов при проверках.
from unittest.mock import patch

# Подключаем playwright.sync_api.
from playwright.sync_api import sync_playwright

# Подключаем проверяемые функции сборщика.
from collect_yandex import (
    # Подключаем функцию или константу сборщика для проверки.
    EXTRACT,
    # Подключаем функцию или константу сборщика для проверки.
    cache_complete,
    # Подключаем функцию или константу сборщика для проверки.
    canonical_url,
    # Подключаем функцию или константу сборщика для проверки.
    collect,
    # Подключаем функцию или константу сборщика для проверки.
    main,
    # Подключаем функцию или константу сборщика для проверки.
    retry_seconds,
    # Подключаем функцию или константу сборщика для проверки.
    review_key,
)

# Подключаем чтение встроенного JSON из HTML.
from extract_saved_html import extract


# Задаём вымышленный идентификатор для проверок.
OID = "123456789"
# Получаем адрес страницы отзывов для вымышленной организации.
URL = canonical_url(OID)[1]


# Создаём HTML с вымышленными отзывами для автономных проверок.
def example_html(count=2, declared=5):
    # Подготавливаем список отзывов для обработки.
    reviews = [
        {
            # Сохраняем вымышленный идентификатор организации.
            "businessId": OID,
            # Сохраняем вымышленный идентификатор отзыва.
            "reviewId": f"review-{i}",
            # Сохраняем отображаемое имя автора.
            "author": {"name": f"Автор {i}", "publicId": f"user-{i}"},
            # Сохраняем дату вымышленного отзыва.
            "updatedTime": "2026-09-01",
            # Сохраняем текст отзыва.
            "text": f"Полный текст отзыва {i}. Продолжение.",
            # Сохраняем оценку отдельного отзыва.
            "rating": i % 5 + 1,
        }
        # Последовательно обрабатываем записи указанного набора.
        for i in range(count)
    ]
    # Выбираем организацию с заданным идентификатором.
    organization = {
        # Сохраняем идентификатор записи.
        "id": OID,
        # Сохраняем название вымышленной организации.
        "title": "Пример организации",
        # Сохраняем координаты вымышленной организации.
        "coordinates": [37.6, 55.7],
        # Сохраняем оценки и счётчики вымышленной организации.
        "ratingData": {"ratingValue": 4.5, "reviewCount": declared},
        # Сохраняем встроенный список вымышленных отзывов.
        "reviewResults": {"reviews": reviews},
    }
    # Разбираем встроенный JSON состояния публичной страницы.
    state = {"stack": [{"results": {"items": [organization]}}]}
    # Формируем вымышленные DOM-карточки с сокращёнными текстами.
    cards = "".join(
        # Задаём HTML-разметку или выражение проверки DOM.
        f'<div class="business-reviews-card-view__review">'
        # Задаём HTML-разметку или выражение проверки DOM.
        f'<span itemprop="author"><span itemprop="name">Автор {i}</span></span>'
        # Задаём HTML-разметку или выражение проверки DOM.
        f'<a class="business-review-view__link" href="https://yandex.ru/maps/user/user-{i}"></a>'
        # Задаём HTML-разметку или выражение проверки DOM.
        f'<meta itemprop="datePublished" content="2026-09-01">'
        # Задаём HTML-разметку или выражение проверки DOM.
        f'<meta itemprop="ratingValue" content="{i % 5 + 1}">'
        # Задаём HTML-разметку или выражение проверки DOM.
        f'<div class="business-review-view__body"><span class="spoiler-view__text _collapsed">'
        # Задаём HTML-разметку или выражение проверки DOM.
        f'<span class="spoiler-view__text-container">Полный текст отзыва {i}.</span></span></div></div>'
        # Последовательно обрабатываем записи указанного набора.
        for i in range(count)
    )
    # Сообщаем, что условие готовности или наличия проверки не выполнено.
    return f'<script class="state-view" type="application/json">{json.dumps(state, ensure_ascii=False)}</script><div class="business-reviews-card-view__title">Отзывы {declared}</div>{cards}'


# Подменяем сетевые действия браузера заранее заданными ответами.
def fake_page(html, batch, status=200):
    # Сохраняем обработчики событий для имитации HTTP-ответа.
    callbacks = {}
    # Получаем ответ открытия страницы или его автономную подмену.
    response = SimpleNamespace(
        # Задаём код ответа или причину остановки.
        status=status,
        # Задаём адрес подменённой страницы.
        url=URL,
        # Имитируем ответ основного HTML-документа.
        request=SimpleNamespace(resource_type="document"),
        # Имитируем серверный заголовок Retry-After.
        headers={"retry-after": "120"},
    )
    # Возвращаем подмену страницы с управляемыми ответами.
    return SimpleNamespace(
        # Задаём адрес подменённой страницы.
        url=URL,
        # Подменяем действие браузера без обращения к внешней сети.
        route=lambda *args: None,
        # Сохраняем callback для имитации события ответа.
        on=lambda event, callback: callbacks.update({event: callback}),
        # Имитируем открытие страницы и событие HTTP-ответа.
        goto=lambda *args, **kwargs: (callbacks["response"](response) or response),
        # Подменяем действие браузера без обращения к внешней сети.
        unroute=lambda *args: None,
        # Подменяем действие браузера без обращения к внешней сети.
        remove_listener=lambda *args: None,
        # Имитируем обычный текст страницы без признаков CAPTCHA.
        locator=lambda *args: SimpleNamespace(
            # Подменённый браузер уже выбрал нужный порядок отзывов.
            wait_for=lambda **kwargs: None,
            # Имитируем обычный текст страницы без признаков CAPTCHA.
            inner_text=lambda **kwargs: "По новизне"
            # Возвращаем подмену страницы с управляемыми ответами.
            if "ranking" in args[0]
            # Возвращаем подмену страницы с управляемыми ответами.
            else "Пример организации",
        ),
        # Подменяем действие браузера без обращения к внешней сети.
        wait_for_selector=lambda *args, **kwargs: None,
        # Контролируемые сценарии не ждут реальные браузерные паузы.
        wait_for_timeout=lambda milliseconds: None,
        # Подтверждаем проверочные даты уже выбранной сортировки.
        wait_for_function=lambda *args, **kwargs: None,
        # Возвращаем вымышленный HTML для проверки извлечения.
        content=lambda: html,
        # Возвращаем заранее подготовленный DOM вместо сетевого чтения.
        evaluate=lambda expression, arg: batch if expression == EXTRACT else 0,
    )


# Объединяем автономные проверки сборщика.
class ReviewTests(unittest.TestCase):
    # Выполняем этот метод один раз для всего класса проверок.
    @classmethod
    # Открываем один браузер для всех автономных проверок DOM.
    def setUpClass(cls):
        # Запускаем Playwright один раз для всех проверок.
        cls.playwright = sync_playwright().start()
        # Задаём параметры запуска Chromium.
        options = (
            # Для автономных проверок допускаем явно указанный Chromium.
            {"executable_path": os.environ["REVIEW_CHROMIUM_PATH"]}
            # Используем внешнюю версию Chromium только при заданной переменной окружения.
            if os.environ.get("REVIEW_CHROMIUM_PATH")
            # Иначе используем Chromium, установленный Playwright.
            else {}
        )
        # Запускаем общий браузер для проверок.
        cls.browser = cls.playwright.chromium.launch(**options)
        # Открываем страницу без исполнения внешних сценариев.
        cls.page = cls.browser.new_page(java_script_enabled=False)
        # Запрещаем любые сетевые загрузки во время автономных проверок.
        cls.page.route("**/*", lambda route: route.abort())

    # Выполняем этот метод один раз для всего класса проверок.
    @classmethod
    # Освобождаем браузер и процесс Playwright после проверок.
    def tearDownClass(cls):
        # Закрываем браузер после проверок.
        cls.browser.close()
        # Останавливаем Playwright после закрытия браузера.
        cls.playwright.stop()

    # Проверяем объединение оценок из DOM и полных текстов из HTML.
    def test_dom_and_full_html(self):
        # Подготавливаем HTML для автономной проверки.
        html = example_html()
        # Загружаем вымышленный HTML вместо обращения к внешнему сайту.
        self.page.set_content(html)
        # Читаем отзывы и счётчики из текущего DOM страницы.
        batch = self.page.evaluate(EXTRACT, 2)
        # Проверяем список отзывов.
        self.assertEqual([r["rating"] for r in batch["reviews"]], [1, 2])
        # Изолируем результаты проверки во временной папке.
        with TemporaryDirectory() as directory:
            # Выбираем JSON-файл результатов одной организации.
            target = Path(directory) / "result.json"
            # Подготавливаем результат обработки и показатели полноты.
            result = collect(fake_page(html, batch), OID, URL, target, 2, 5)
            # Проверяем состояние обработки.
            self.assertEqual(result["status"], "capped")
            # Проверяем число отзывов с полными обязательными полями.
            self.assertEqual(result["valid_reviews"], 2)
            # Проверяем ожидаемое поведение обработчика.
            self.assertEqual(result["truncated_count"], 0)
            # Проверяем число раскрытых текстов.
            self.assertEqual(result["expanded_texts"], 0)
            # Проверяем список отзывов.
            self.assertEqual(len(result["reviews"]), 2)
            # Проверяем ожидаемое поведение обработчика.
            self.assertTrue(
                # Сопоставляем полученные значения с ожидаемым результатом.
                all(r["text"].startswith("Полный текст") for r in result["reviews"])
            )
            # Проверяем ожидаемое поведение обработчика.
            self.assertTrue(cache_complete(result, OID, 2))
            # Проверяем ожидаемое поведение обработчика.
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), result)

    # Проверяем, что рейтинг организации не подставляется вместо оценки отзыва.
    def test_global_rating_is_not_review_rating(self):
        # Загружаем вымышленный HTML вместо обращения к внешнему сайту.
        self.page.set_content(
            # Задаём HTML-разметку или выражение проверки DOM.
            '<meta itemprop="ratingValue" content="5"><div class="business-reviews-card-view__review"><div itemprop="reviewBody">Текст</div><div>Ответ организации</div></div>'
        )
        # Получаем отдельный отзыв для проверки его полей.
        row = self.page.evaluate(EXTRACT, 1)["reviews"][0]
        # Проверяем оценку отдельного отзыва.
        self.assertIsNone(row["rating"])
        # Проверяем текст отзыва.
        self.assertEqual(row["text"], "Текст")

    # Проверяем сохранение результата и остановку при ограничении или CAPTCHA.
    def test_429_and_captcha_stop(self):
        # Изолируем результаты проверки во временной папке.
        with TemporaryDirectory() as directory:
            # Выбираем JSON-файл результатов одной организации.
            target = Path(directory) / "result.json"
            # Подготавливаем результат обработки и показатели полноты.
            result = collect(fake_page("", {}, 429), OID, URL, target, 2, 5)
            # Проверяем состояние обработки.
            self.assertEqual(result["status"], "rate_limited")
            # Проверяем ожидаемое поведение обработчика.
            self.assertEqual(result["retry_after"], "120")
            # Подготавливаем страницу для чтения DOM или автономную подмену браузера.
            page = fake_page("", {})
            # Имитируем перенаправление на страницу CAPTCHA.
            page.url = "https://yandex.ru/showcaptcha"
            # Подготавливаем результат обработки и показатели полноты.
            result = collect(page, OID, URL, target, 2, 5)
            # Проверяем состояние обработки.
            self.assertEqual(result["status"], "captcha")

    # Проверяем пустую карточку и явное обозначение расхождения счётчиков.
    def test_zero_reviews_and_counter_mismatch(self):
        # Изолируем результаты проверки во временной папке.
        with TemporaryDirectory() as directory:
            # Выбираем JSON-файл результатов одной организации.
            target = Path(directory) / "result.json"
            # Подготавливаем результат обработки и показатели полноты.
            result = collect(
                # Подготавливаем результат обработки и показатели полноты.
                fake_page(example_html(0, 0), {"company": {"reviews_count": 0}, "reviews": []}),
                # Подготавливаем результат обработки и показатели полноты.
                OID,
                # Подготавливаем результат обработки и показатели полноты.
                URL,
                # Подготавливаем результат обработки и показатели полноты.
                target,
                # Подготавливаем результат обработки и показатели полноты.
                2,
                # Передаём допустимую паузу или небольшой объём автономной проверки.
                5,
            )
            # Проверяем состояние обработки.
            self.assertEqual(result["status"], "complete")
            # Проверяем ожидаемое поведение обработчика.
            self.assertTrue(cache_complete(result, OID, 2))
            # Подготавливаем HTML для автономной проверки.
            html = example_html(2, 1)
            # Загружаем вымышленный HTML вместо обращения к внешнему сайту.
            self.page.set_content(html)
            # Подготавливаем результат обработки и показатели полноты.
            result = collect(
                # Используем автономную подмену страницы в проверке сборщика.
                fake_page(html, self.page.evaluate(EXTRACT, 2)),
                # Передаём вымышленный идентификатор организации.
                OID,
                # Передаём вымышленный адрес страницы отзывов.
                URL,
                # Передаём путь сохранения результатов текущей организации.
                target,
                # Ограничиваем автономный сбор двумя отзывами.
                2,
                # Задаём допустимую паузу прокрутки для проверяемого вызова.
                5,
            )
            # Проверяем состояние обработки.
            self.assertEqual(result["status"], "count_mismatch")
            # Проверяем список отзывов.
            self.assertEqual(len(result["reviews"]), 2)
            # Проверяем ожидаемое поведение обработчика.
            self.assertFalse(cache_complete(result, OID, 2))

    # Проверяем отбраковку неполных полей и пропуск запуска браузера при готовом кэше.
    def test_cache_validation_and_no_browser_on_hit(self):
        # Получаем отдельный отзыв для проверки его полей.
        row = {
            # Сохраняем идентификатор записи.
            "id": "one",
            # Сохраняем текст отзыва.
            "text": "Текст",
            # Сохраняем дату отзыва.
            "date": "2026-09-01",
            # Сохраняем оценку отдельного отзыва.
            "rating": 5,
            # Сохраняем признак сокращённого текста.
            "text_truncated": False,
        }
        # Читаем готовый результат для проверки возможности повторного использования.
        cached = {
            # Сохраняем версию формата JSON.
            "schema_version": 1,
            # Сохраняем идентификатор организации.
            "organization_id": OID,
            # Сохраняем состояние обработки.
            "status": "complete",
            # Сохраняем сведения об организации.
            "company": {"reviews_count": 1},
            # Сохраняем список отзывов.
            "reviews": [row],
        }
        # Проверяем ожидаемое поведение обработчика.
        self.assertTrue(cache_complete(cached, OID, 10))
        # Последовательно обрабатываем записи указанного набора.
        for field, value in [
            # Задаём случай неполного или неверного поля для проверки кэша.
            ("date", None),
            # Задаём случай неполного или неверного поля для проверки кэша.
            ("rating", True),
            # Задаём случай неполного или неверного поля для проверки кэша.
            ("text_truncated", None),
            # Задаём случай неполного или неверного поля для проверки кэша.
            ("id", None),
        ]:
            # Создаём неполный результат, который нельзя считать готовым кэшем.
            broken = dict(cached, reviews=[dict(row, **{field: value})])
            # Проверяем ожидаемое поведение обработчика.
            self.assertFalse(cache_complete(broken, OID, 10))
        # Изолируем результаты проверки во временной папке.
        with TemporaryDirectory() as directory:
            # Записываем вымышленный кэш во временную папку.
            (Path(directory) / f"{OID}.json").write_text(
                # Преобразуем данные в JSON с заданными параметрами записи.
                json.dumps(cached),
                # Записываем файл в UTF-8 для сохранения кириллицы.
                encoding="utf-8",
            )
            # Подменяем параметры и запуск браузера для автономной проверки.
            with patch(
                # Передаём проверяемому запуску вымышленную организацию и временную папку.
                "sys.argv",
                # Передаём запуску вымышленную организацию и временную папку результатов.
                ["collect_yandex.py", OID, "--output", directory]
                # Перехватываем запуск Playwright, чтобы проверить отсутствие обращения к браузеру.
            ), patch("collect_yandex.sync_playwright") as browser_start:
                # Запускаем обработку параметров и очередь организаций.
                main()
                # Проверяем, что готовый кэш не запускает браузер.
                browser_start.assert_not_called()

    # Проверяем ключ отзыва, допустимые адреса и разбор Retry-After.
    def test_identity_urls_and_retry_header(self):
        # При отсутствии автора пробелы по краям не должны создавать два ключа.
        anonymous = {"date": "2026-09-01", "text": "Полный текст\n"}
        # Проверяем объединение HTML и DOM анонимного отзыва.
        self.assertEqual(
            # Проверяем объединение HTML и DOM анонимного отзыва.
            review_key(OID, anonymous),
            # Проверяем объединение HTML и DOM анонимного отзыва.
            review_key(OID, dict(anonymous, text="Полный текст")),
        )
        # Надпись «Анонимный отзыв» в DOM должна соответствовать отсутствующему имени в HTML.
        self.assertEqual(
            # Надпись «Анонимный отзыв» в DOM должна соответствовать отсутствующему имени в HTML.
            review_key(OID, anonymous),
            # Надпись «Анонимный отзыв» в DOM должна соответствовать отсутствующему имени в HTML.
            review_key(OID, dict(anonymous, author="Анонимный отзыв", text="Полный текст")),
        )
        # Получаем отдельный отзыв для проверки его полей.
        row = {
            # Сохраняем публичную ссылку на профиль автора.
            "author_url": "user/example",
            # Сохраняем дату отзыва.
            "date": "2026-09-01",
            # Сохраняем текст отзыва.
            "text": "Короткий текст",
        }
        # Проверяем ожидаемое поведение обработчика.
        self.assertEqual(
            # Проверяем одинаковый ключ короткой и полной версии отзыва.
            review_key(OID, row),
            # Сравниваем ключ полной версии отзыва с ключом короткой версии.
            review_key(OID, dict(row, text="Полный текст")),
        )
        # Проверяем ожидаемое поведение обработчика.
        self.assertEqual(canonical_url(URL)[0], OID)
        # Последовательно обрабатываем записи указанного набора.
        for invalid in [
            # Проверяем отклонение незащищённой HTTP-ссылки.
            "http://yandex.ru/maps/org/-/123/",
            # Проверяем отклонение постороннего домена.
            "https://example.org/maps/org/-/123/",
            # Проверяем отклонение ссылки без идентификатора организации.
            "https://yandex.ru/maps/",
        ]:
            # Ожидаем отклонения недопустимой ссылки.
            with self.assertRaises(ValueError):
                # Передаём недопустимую ссылку и ожидаем ValueError.
                canonical_url(invalid)
        # Проверяем ожидаемое поведение обработчика.
        self.assertEqual(retry_seconds("120"), 120)
        # Проверяем ожидаемое поведение обработчика.
        self.assertEqual(retry_seconds("bad"), 0)
        # Проверяем ожидаемое поведение обработчика.
        self.assertEqual(extract(example_html(), OID)["loaded_reviews"], 2)

    # Анонимные отзывы за одну дату должны раскрываться и сохраняться раздельно.
    def test_anonymous_short_full_and_expansion(self):
        # Создаём два отзыва с одной датой и разными текстами.
        html = example_html(2, 2)
        # Читаем вымышленное встроенное состояние.
        state = json.loads(re.search(r"<script[^>]*>(.*?)</script>", html, re.S)[1])
        # Оставляем в HTML только первый полный текст, второй доступен только после раскрытия DOM.
        reviews = state["stack"][0]["results"]["items"][0]["reviewResults"]["reviews"]
        # Первый отзыв не имеет публичного профиля или имени.
        reviews[0]["author"] = {}
        # Второй отзыв отсутствует в первой встроенной порции.
        del reviews[1:]
        # Сохраняем карточки DOM отдельно от нового состояния.
        cards = html.split("</script>", 1)[1]
        # Убираем публичные ссылки обоих анонимных авторов.
        cards = re.sub(r'<a class="business-review-view__link"[^>]*></a>', "", cards)
        # Отображение анонимного автора не должно создать второй ключ того же текста.
        cards = cards.replace("Автор 0", "Анонимный отзыв").replace("Автор 1", "Анонимный отзыв")
        # Короткие тексты должны быть префиксами их полных версий.
        cards = cards.replace("Короткий текст 0", "Полный текст отзыва 0.").replace(
            # Короткие тексты должны быть префиксами их полных версий.
            "Короткий текст 1",
            # Короткие тексты должны быть префиксами их полных версий.
            "Полный текст отзыва 1.",
        )
        # Формируем первую порцию HTML и два свёрнутых DOM-отзыва.
        html = (
            # Формируем первую порцию HTML и два свёрнутых DOM-отзыва.
            '<script class="state-view" type="application/json">'
            # Формируем первую порцию HTML и два свёрнутых DOM-отзыва.
            + json.dumps(state)
            # Формируем первую порцию HTML и два свёрнутых DOM-отзыва.
            + "</script>"
            # Формируем первую порцию HTML и два свёрнутых DOM-отзыва.
            + cards
        )
        # Создаём отдельную страницу для управляемых обработчиков раскрытия.
        page = self.browser.new_page()
        # Любые сетевые обращения в автономной проверке запрещены.
        page.route("**/*", lambda route: route.abort())
        # Освобождаем страницу при любом исходе проверки.
        try:
            # Загружаем только вымышленную разметку.
            page.set_content(html)
            # Добавляем управляемые кнопки раскрытия, повторяющие изменение DOM страницы.
            page.evaluate(
                # Добавляем управляемые кнопки раскрытия, повторяющие изменение DOM страницы.
                "() => document.querySelectorAll('.business-reviews-card-view__review').forEach((card,i) => {const button=document.createElement('button');button.className='business-review-view__expand';button.textContent='Читать дальше';button.addEventListener('click',()=>{card.querySelector('.spoiler-view__text-container').textContent='Полный текст отзыва '+i+'. Продолжение.';card.querySelector('.spoiler-view__text').classList.remove('_collapsed');});card.append(button);})"
            )
            # Имитируем только открытие публичной страницы, сохраняя реальное чтение и раскрытие DOM.
            proxy = fake_page(html, {})
            # Все браузерные выражения выполняются над этой страницей.
            proxy.evaluate = page.evaluate
            # Раскрытие синхронное, поэтому дополнительное ожидание не требуется.
            proxy.wait_for_timeout = lambda *args: None
            # Изолируем сохранённый результат.
            with TemporaryDirectory() as folder:
                # Выполняем настоящий сбор и объединение HTML с DOM.
                result = collect(proxy, OID, URL, Path(folder) / "result.json", 2, 0)
            # Полные два отзыва не должны превратиться в дубликаты коротких версий.
            self.assertEqual(result["status"], "complete")
            # У каждого текста сохраняется отдельный ключ.
            self.assertEqual(len(result["reviews"]), 2)
            # Оба текста полные и оценки индивидуальные.
            self.assertEqual(result["valid_reviews"], 2)
            # Второй анонимный отзыв за ту же дату действительно раскрыт.
            self.assertTrue(all("Продолжение." in row["text"] for row in result["reviews"]))
        # Закрываем только страницу этого автономного сценария.
        finally:
            # Общий браузер класса остаётся доступен другим проверкам.
            page.close()

    # Проверяем переключение публичного меню в настоящем браузере без сети.
    def test_newest_sort_menu_in_browser(self):
        # Импортируем управление сортировкой для отдельной проверки интерфейса.
        from collect_yandex import select_newest

        # Открываем локальную страницу с разрешёнными обработчиками событий.
        page = self.browser.new_page()
        # Запрещаем любые внешние загрузки.
        page.route("**/*", lambda route: route.abort())
        # Готовим элементы с теми же селекторами, что у публичной страницы.
        html = '<div class="business-reviews-card-view__ranking"><div class="rating-ranking-view">По умолчанию</div></div><div class="rating-ranking-view__popup-line" aria-label="Сортировать отзывы" style="display:none">По новизне</div><div class="business-reviews-card-view__review"><meta itemprop="datePublished" content="2026-09-01"></div><div class="business-reviews-card-view__review"><meta itemprop="datePublished" content="2026-09-02"></div>'
        # Закрываем страницу при любом исходе теста.
        try:
            # Загружаем вымышленную популярную выдачу с неупорядоченными датами.
            page.set_content(html)
            # Два обычных клика должны изменить меню и порядок карточек.
            page.evaluate(
                """() => {
              // Находим кнопку в блоке отзывов.
              const button=document.querySelector('.rating-ranking-view');
              // Находим скрытый пункт меню.
              const option=document.querySelector('.rating-ranking-view__popup-line');
              // Открытие меню делает выбор доступным.
              button.addEventListener('click',()=>{option.style.display='block';});
              // Выбор меняет подпись и переставляет отзывы.
              option.addEventListener('click',()=>{
                // Публичная подпись подтверждает выбранный порядок.
                button.textContent='По новизне';
                // Переставляем старый отзыв после нового.
                document.body.appendChild(document.querySelector('.business-reviews-card-view__review'));
                // Закрываем меню после выбора.
                option.style.display='none';
              });
            }"""
            )
            # Выполняем настоящую функцию управления интерфейсом.
            select_newest(page)
            # Подпись меню должна подтвердить новый порядок.
            self.assertEqual(page.locator(".rating-ranking-view").inner_text(), "По новизне")
            # Проверяем порядок видимых дат после переключения.
            self.assertEqual(
                # Проверяем порядок видимых дат после переключения.
                page.locator('[itemprop="datePublished"]').first.get_attribute("content"),
                # Проверяем порядок видимых дат после переключения.
                "2026-09-02",
            )
        # Не оставляем вкладку после завершения проверки.
        finally:
            # Освобождаем локальную страницу.
            page.close()

    # Проверяем ожидание новой карточки при постоянном числе узлов в настоящем DOM.
    def test_virtualized_tail_wait_in_browser(self):
        # Используем тот же сценарий ожидания, что у живого сборщика.
        from collect_yandex import TAIL, TAIL_CHANGED, CARDS

        # Страница остаётся локальной и не выполняет внешние загрузки.
        self.page.set_content(example_html(2, 3))
        # Запоминаем последний текущий отзыв.
        before = self.page.evaluate(TAIL, CARDS)
        # Заменяем старую карточку новой, сохранив размер окна.
        self.page.evaluate(
            """() => {
          // Получаем два исходных отзыва локальной страницы.
          const cards=[...document.querySelectorAll('.business-reviews-card-view__review')];
          // Новая карточка имеет другой текст, как у анонимного отзыва с той же датой.
          const next=cards[1].cloneNode(true);
          // Различаем записи по содержанию, а не количеству элементов.
          next.querySelector('.spoiler-view__text-container').textContent='Новый последний отзыв';
          // Удаляем ушедшую из окна первую карточку.
          cards[0].remove();
          // Добавляем новую последнюю карточку.
          cards[1].parentNode.appendChild(next);
        }"""
        )
        # Ожидание должно завершиться, хотя число карточек не выросло.
        self.page.wait_for_function(
            # Ожидание должно завершиться, хотя число карточек не выросло.
            TAIL_CHANGED,
            # Ожидание должно завершиться, хотя число карточек не выросло.
            arg={"selector": CARDS, "before": before},
            # Ожидание должно завершиться, хотя число карточек не выросло.
            timeout=1000,
        )
        # Размер виртуального окна действительно остался прежним.
        self.assertEqual(self.page.locator(CARDS).count(), 2)


# Выполняем запуск только при прямом вызове файла.
if __name__ == "__main__":
    # Запускаем все автономные проверки и выводим итог.
    unittest.main()
