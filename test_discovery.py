"""Автономные проверки пространственного поиска и передачи карточек сборщику отзывов."""

# Создаём вымышленные HTML-состояния и проверяем запись JSON.
import json

# Создаём совместимый ключ поискового снимка для проверки возобновления.
import hashlib

# Разрешаем явно выбранную версию Chromium для проверок.
import os

# Запускаем проверки средствами стандартной библиотеки.
import unittest

# Создаём пути результатов внутри временной папки.
from pathlib import Path

# Изолируем результаты каждой проверки.
from tempfile import TemporaryDirectory

# Подменяем браузерную страницу управляемыми ответами.
from types import SimpleNamespace

# Подменяем только сетевую функцию при проверке общей очереди.
from unittest.mock import patch

# Читаем вымышленный DOM в Chromium без внешней сети.
from playwright.sync_api import sync_playwright

# Проверяем общую очередь отзывов независимо от браузерных загрузок.
from collect_yandex import collect_many

# Подключаем фильтрацию, извлечение и проверку поискового кэша.
from discover_yandex import (
    # Подключаем чтение ссылок и координат в браузере.
    SEARCH_DOM,
    # Подключаем проверку координат и фактических рубрик.
    classify_candidate,
    # Подключаем поиск одной категории с сохранением прогресса.
    discover,
    # Подключаем чтение организаций из встроенного HTML.
    extract_search_html,
    # Подключаем единый локальный справочник категорий.
    load_categories,
    # Подключаем дополнение организаций полями текущего DOM.
    merge_search_dom,
    # Подключаем разбор ключей категорий и свободных запросов.
    resolve_searches,
    # Подключаем проверку совместимости поискового снимка.
    search_cache_complete,
    # Подключаем создание адреса публичной страницы поиска.
    search_url,
    # Подключаем проверку отдельной пары координат.
    valid_coordinates,
    # Подключаем проверку границ выбранной территории.
    validate_bbox,
)

# Проверяем запуск по готовому поисковому кэшу без браузера.
from discover_yandex import main as discover_main

# Выбираем небольшой вымышленный прямоугольник.
BBOX = [37.5, 55.6, 37.7, 55.8]
# Используем готовую категорию для проверки фактических рубрик.
CAFE = resolve_searches(["cafe"], [])[0]


# Создаём состояние поиска с организацией внутри, снаружи и без координат.
def fixture_html():
    # Используем вымышленные названия и идентификаторы.
    items = [
        # Кафе внутри прямоугольника подходит под все условия.
        {
            # Сохраняем вымышленный идентификатор проверки.
            "id": "101",
            # Сохраняем тип объекта для отделения организаций от адресов.
            "type": "business",
            # Сохраняем вымышленное название проверки.
            "title": "Кафе А",
            # Сохраняем координаты вымышленного результата.
            "coordinates": [37.6, 55.7],
            # Сохраняем рубрики вымышленного результата.
            "categories": [{"name": "Кафе"}],
        },
        # Кафе снаружи прямоугольника не должно попасть в очередь отзывов.
        {
            # Сохраняем вымышленный идентификатор проверки.
            "id": "102",
            # Сохраняем тип объекта для отделения организаций от адресов.
            "type": "business",
            # Сохраняем вымышленное название проверки.
            "title": "Кафе Б",
            # Сохраняем координаты вымышленного результата.
            "coordinates": [38.0, 55.7],
            # Сохраняем рубрики вымышленного результата.
            "categories": [{"name": "Кафе"}],
        },
        # Отсутствующие координаты требуют отдельного обозначения.
        {"id": "103", "type": "business", "title": "Кафе В", "categories": [{"name": "Кафе"}]},
        # Адресный объект не является организацией с карточкой отзывов.
        {"id": "104", "type": "house", "title": "Дом"},
    ]
    # Повторяем структуру публичного HTML без служебных запросов.
    state = {"stack": [{"results": {"items": items}}]}
    # Добавляем DOM-карточку той же организации для проверки объединения.
    card = '<div class="search-business-snippet-view" data-coordinates="37.6,55.7"><a href="https://yandex.ru/maps/org/example/101/"><span class="search-business-snippet-view__title">Кафе А</span></a><span class="search-business-snippet-view__category">Кафе</span></div>'
    # Возвращаем HTML со встроенным состоянием и одной видимой карточкой.
    return f'<script class="state-view" type="application/json">{json.dumps(state)}</script>{card}'


# Имитируем страницу поиска без внешних обращений.
def fake_search_page(html, items, status=200):
    # Храним обработчик, которому передаётся управляемый HTTP-ответ.
    callbacks = {}
    # Имитируем основную публичную страницу поиска.
    response = SimpleNamespace(
        # Имитируем основную публичную страницу поиска.
        status=status,
        # Имитируем основную публичную страницу поиска.
        url="https://yandex.ru/maps/",
        # Имитируем основную публичную страницу поиска.
        request=SimpleNamespace(resource_type="document"),
        # Имитируем основную публичную страницу поиска.
        headers={"retry-after": "120"},
    )
    # Возвращаем методы, используемые модулем поиска.
    return SimpleNamespace(
        # Обычный адрес не содержит признаков CAPTCHA.
        url=response.url,
        # Настройка маршрута не создаёт внешних запросов.
        route=lambda *args: None,
        # Сохраняем callback для имитации ответа.
        on=lambda event, callback: callbacks.update({event: callback}),
        # Открытие страницы вызывает событие ответа.
        goto=lambda *args, **kwargs: (callbacks["response"](response) or response),
        # После проверки обработчики могут быть удалены.
        unroute=lambda *args: None,
        # Удаление слушателя также не требует браузера.
        remove_listener=lambda *args: None,
        # Имитируем видимый текст без проверки пользователя.
        locator=lambda *args: SimpleNamespace(inner_text=lambda **kwargs: "Результаты поиска"),
        # Вымышленная карточка уже доступна без ожидания.
        wait_for_function=lambda *args, **kwargs: None,
        # Паузы не задерживают автономные проверки.
        wait_for_timeout=lambda *args: None,
        # Возвращаем подготовленный HTML.
        content=lambda: html,
        # DOM извлекается из вымышленной страницы; прокрутка ничего не загружает.
        evaluate=lambda expression, *args: items if expression == SEARCH_DOM else False,
    )


# Объединяем проверки отбора и связки поиска с отзывами.
class DiscoveryTests(unittest.TestCase):
    # Один браузер используется всеми проверками DOM этого класса.
    @classmethod
    # Подготавливаем браузер для вымышленных страниц.
    def setUpClass(cls):
        # Запускаем Playwright без посещения Яндекс Карт.
        cls.playwright = sync_playwright().start()
        # Допускаем уже установленный Chromium, если путь указан явно.
        options = (
            # Допускаем уже установленный Chromium, если путь указан явно.
            {"executable_path": os.environ["REVIEW_CHROMIUM_PATH"]}
            # Допускаем уже установленный Chromium, если путь указан явно.
            if os.environ.get("REVIEW_CHROMIUM_PATH")
            # Если файла нет, начинаем без сохранённого результата.
            else {}
        )
        # Запускаем один Chromium.
        cls.browser = cls.playwright.chromium.launch(**options)
        # Отключаем исполнение внешних сценариев в вымышленных страницах.
        cls.page = cls.browser.new_page(java_script_enabled=False)
        # Запрещаем любую внешнюю загрузку во время проверок.
        cls.page.route("**/*", lambda route: route.abort())

    # Освобождение ресурсов выполняется один раз после всего класса.
    @classmethod
    # Закрываем процессы, созданные автономными проверками.
    def tearDownClass(cls):
        # Закрываем Chromium.
        cls.browser.close()
        # Закрываем управляющий процесс Playwright.
        cls.playwright.stop()

    # Проверяем порядок координат, границы и недопустимые значения.
    def test_bbox_and_coordinates(self):
        # Готовый прямоугольник сохраняет исходный порядок координат.
        self.assertEqual(validate_bbox(BBOX), BBOX)
        # Проверяем перевёрнутые границы и NaN.
        for invalid in [[37.7, 55.6, 37.5, 55.8], [37.5, 55.6, float("nan"), 55.8], []]:
            # Такой ввод должен быть отклонён до открытия браузера.
            with self.assertRaises(ValueError):
                # Проверяем конкретный недопустимый прямоугольник.
                validate_bbox(invalid)
        # Координаты DOM преобразуются без перестановки осей.
        self.assertEqual(valid_coordinates("37.6,55.7"), [37.6, 55.7])
        # Бесконечная долгота не должна попасть в пространственный отбор.
        self.assertIsNone(valid_coordinates([float("inf"), 55]))

    # Проверяем все готовые настройки как данные, без массового поиска сайта.
    def test_catalog_and_custom_queries(self):
        # Загружаем локальный список категорий.
        catalog = load_categories()
        # Разрешаем весь справочник за два прохода вместо повторного чтения для каждой рубрики.
        keyed = {item["key"]: item for item in resolve_searches(list(catalog), [])}
        # Повторяющиеся названия документации разрешаются в первое соответствие.
        named = {
            # Повторяющиеся названия документации разрешаются в первое соответствие.
            item["label"]: item
            # Повторяющиеся названия документации разрешаются в первое соответствие.
            for item in resolve_searches([item["label"] for item in catalog.values()], [])
        }
        # Проверяем каждую настройку, включая одноимённые рубрики разных разделов.
        for key, item in catalog.items():
            # Одинаковые названия должны задавать одинаковую поисковую фразу.
            self.assertEqual(
                # Одинаковые названия должны задавать одинаковую поисковую фразу.
                keyed[key]["query"].casefold(), named[item["label"]]["query"].casefold()
            )
            # Номер раздела не меняет фактическую рубрику найденной карточки.
            self.assertEqual(keyed[key]["rubrics"], named[item["label"]]["rubrics"])
            # Предустановка должна содержать фактические рубрики для проверки результата.
            self.assertTrue(item["rubrics"])
        # Повтор ключа не создаёт лишнюю поисковую страницу.
        self.assertEqual(len(resolve_searches(["cafe", "Кафе"], [])), 1)
        # Свободный запрос не выдаётся за проверенную рубрику.
        self.assertEqual(resolve_searches([], ["дом культуры"])[0]["rubrics"], [])
        # Неизвестный тег не подменяется случайным запросом автоматически.
        with self.assertRaises(ValueError):
            # Жилые дома и жилые комплексы различаются.
            resolve_searches(["building=residential"], [])

    # Проверяем объединение HTML и DOM с точным пространственным отбором.
    def test_html_dom_and_filtering(self):
        # Создаём HTML с вымышленными результатами поиска.
        html = fixture_html()
        # Читаем только организации из встроенного состояния.
        rows = extract_search_html(html)
        # Дом без типа business исключён.
        self.assertEqual(set(rows), {"101", "102", "103"})
        # Показываем вымышленную страницу в локальном браузере.
        self.page.set_content(html)
        # Дополняем первую организацию DOM, не создавая дубликата.
        merge_search_dom(rows, self.page.evaluate(SEARCH_DOM))
        # Количество организаций осталось неизменным.
        self.assertEqual(len(rows), 3)
        # Организация внутри области с подходящей рубрикой принимается.
        self.assertEqual(classify_candidate(rows["101"], BBOX, CAFE["rubrics"]), "accepted")
        # Организация снаружи не должна получить отзывы в этой выборке.
        self.assertEqual(classify_candidate(rows["102"], BBOX, CAFE["rubrics"]), "outside_bbox")
        # Неизвестные координаты сохраняются как причина исключения.
        self.assertEqual(
            # Неизвестные координаты сохраняются как причина исключения.
            classify_candidate(rows["103"], BBOX, CAFE["rubrics"]),
            # Неизвестные координаты сохраняются как причина исключения.
            "missing_coordinates",
        )
        # Запрос кафе не должен принимать школу только по положению.
        self.assertEqual(
            # Запрос кафе не должен принимать школу только по положению.
            classify_candidate(rows["101"], BBOX, ["Общеобразовательная школа"]),
            # Запрос кафе не должен принимать школу только по положению.
            "rubric_mismatch",
        )

    # Проверяем сохранение поиска, HTTP 429 и совместимость поискового кэша.
    def test_search_and_cache(self):
        # Читаем DOM вымышленной страницы без внешней сети.
        self.page.set_content(fixture_html())
        # Подготавливаем результат DOM для управляемой подмены страницы.
        items = self.page.evaluate(SEARCH_DOM)
        # Изолируем JSON этой проверки.
        with TemporaryDirectory() as directory:
            # Выбираем файл поискового прогресса.
            target = Path(directory) / "search.json"
            # Ограничиваем поиск одной подходящей организацией.
            result = discover(fake_search_page(fixture_html(), items), BBOX, CAFE, target, 1)
            # Достижение лимита должно быть обозначено явно.
            self.assertEqual(result["status"], "capped")
            # Подтверждённая организация передаётся с исходным идентификатором.
            self.assertEqual(result["organizations"][0]["organization_id"], "101")
            # За пределами области остался ровно один кандидат.
            self.assertEqual(result["selection_counts"]["outside_bbox"], 1)
            # Проверяем сохранённый снимок по тем же параметрам.
            self.assertTrue(search_cache_complete(result, BBOX, CAFE, 1))
            # При смене прямоугольника кэш не подходит.
            self.assertFalse(search_cache_complete(result, [37.6, 55.6, 37.7, 55.8], CAFE, 1))
            # Готовый поиск должен повторно использоваться без запуска Playwright.
            output = Path(directory) / "cached_run"
            # Подготавливаем структуру каталога сохранённых поисковых снимков.
            (output / "search").mkdir(parents=True)
            # Используем ту же формулу ключа, что и самостоятельный запуск.
            digest = hashlib.sha256(
                # Используем ту же формулу ключа, что и самостоятельный запуск.
                json.dumps([BBOX, CAFE, 1], sort_keys=True, ensure_ascii=False).encode()
                # Используем ту же формулу ключа, что и самостоятельный запуск.
            ).hexdigest()[:16]
            # Сохраняем проверенный поисковый результат.
            (output / "search" / f"{digest}.json").write_text(json.dumps(result), encoding="utf-8")
            # Передаём запуску параметры, соответствующие готовому снимку.
            argv = [
                # Передаём запуску параметры, соответствующие готовому снимку.
                "discover_yandex.py",
                # Задаём имя параметра командной строки.
                "--bbox",
                # Передаём запуску параметры, соответствующие готовому снимку.
                *map(str, BBOX),
                # Задаём имя параметра командной строки.
                "--categories",
                # Передаём запуску параметры, соответствующие готовому снимку.
                "cafe",
                # Задаём имя параметра командной строки.
                "--max-orgs",
                # Передаём запуску параметры, соответствующие готовому снимку.
                "1",
                # Задаём имя параметра командной строки.
                "--output",
                # Передаём запуску параметры, соответствующие готовому снимку.
                str(output),
            ]
            # Перехватываем создание браузера, не меняя проверку совместимости данных.
            with patch("sys.argv", argv), patch(
                # Перехватываем создание браузера, не меняя проверку совместимости данных.
                "discover_yandex.sync_playwright"
            # Перехватываем создание браузера, не меняя проверку совместимости данных.
            ) as browser_start:
                # Запускаем поиск с готовым совместимым кэшем.
                discover_main()
                # Ни Playwright, ни Chromium не должны запускаться.
                browser_start.assert_not_called()
            # Ошибка 429 должна остановить поиск до чтения DOM.
            result = discover(fake_search_page("", [], 429), BBOX, CAFE, target, 1)
            # Ограничение нового открытия не должно стирать ранее найденных кандидатов.
            self.assertEqual(len(result["candidates"]), 3)
            # Кандидаты старой попытки без подтверждения не передаются сборщику отзывов.
            self.assertEqual(result["organizations"], [])
            # Проверяем статус ограничения доступа.
            self.assertEqual(result["status"], "rate_limited")
            # Проверяем сохранение серверной задержки.
            self.assertEqual(result["retry_after"], "120")
            # Файл на диске соответствует окончательному результату.
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), result)

    # Проверяем, что общий обработчик не посещает следующую карточку после ограничения.
    def test_review_queue_stops(self):
        # Создаём результат, который вернул бы сборщик после HTTP 429.
        limited = {
            # Сохраняем идентификатор организации Яндекса.
            "organization_id": "101",
            # Сохраняем состояние поиска.
            "status": "rate_limited",
            # Сохраняем число отзывов с полными полями.
            "valid_reviews": 0,
            # Сохраняем число свёрнутых текстов.
            "truncated_count": 0,
            # Сохраняем время попытки.
            "elapsed_seconds": 0,
            # Сохраняем число прокруток.
            "scrolls": 0,
            # Сохраняем число раскрытых текстов.
            "expanded_texts": 0,
            # Сохраняем время начала попытки.
            "collected_at": "2026-10-02",
            # Сохраняем показатели браузерных запросов.
            "network": {"issues": []},
        }
        # Все файлы результатов находятся во временной папке.
        with TemporaryDirectory() as directory:
            # Задаём две карточки в очереди.
            jobs = [
                # Задаём две карточки в очереди.
                ("101", "https://yandex.ru/maps/org/-/101/reviews/", Path(directory) / "101.json"),
                # Задаём две карточки в очереди.
                ("102", "https://yandex.ru/maps/org/-/102/reviews/", Path(directory) / "102.json"),
            ]
            # Подменяем только сетевой сбор одной карточки.
            with patch("collect_yandex.collect", return_value=limited) as collect_one:
                # Запускаем общую очередь без повторов после ограничения.
                outcomes = collect_many(None, jobs, 5, 5, 20, 0, "Chrome")
                # Следующая карточка не должна быть открыта.
                self.assertEqual(collect_one.call_count, 1)
                # Ведомость содержит только реально обработанную карточку.
                self.assertEqual(len(outcomes), 1)
        # Поиск использует публичный адрес карты, а не служебный endpoint.
        self.assertTrue(search_url(BBOX, "кафе").startswith("https://yandex.ru/maps/?"))


# Прямой запуск проверяет модуль без доступа к Яндекс Картам.
if __name__ == "__main__":
    # Выполняем все автономные проверки этого файла.
    unittest.main()
