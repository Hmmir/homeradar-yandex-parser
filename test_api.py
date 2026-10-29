"""Проверка таблиц, пространственной связи и вызова установленного пакета."""

# Подготавливаем совместимый ключ готового поискового снимка.
import hashlib

# Выбираем Chromium из окружения автономных проверок.
import os

# Сохраняем входные файлы без сетевого доступа.
import json

# Представляем исход завершения существующего сценария.
import subprocess

# Создаём отдельную папку результатов для каждой проверки.
import tempfile

# Используем существующий формат автономных проверок.
import unittest

# Читаем и записываем временные снимки.
from pathlib import Path

# Подменяем только запуск процесса в проверках частичного результата.
from unittest.mock import patch

# Проверяем большой список категорий в настоящем браузере на локальном содержимом.
from playwright.sync_api import sync_playwright

# Проверяем именно установленный публичный интерфейс пакета.
from yandex_maps_reviews import collect_area, list_categories, list_cities, load_results

# Проверяем подготовку всех вариантов территории без запуска браузера.
from yandex_maps_reviews.api import _area_config


# Подготавливаем небольшую вымышленную территорию и карточку организации.
BBOX = [37.1, 55.1, 37.11, 55.11]

# Все сведения относятся к вымышленной организации внутри прямоугольника.
ORGANIZATION = {
    # Сохраняем идентификатор организации Яндекса.
    "organization_id": "101",
    # Сохраняем адрес публичной карточки.
    "url": "https://yandex.ru/maps/org/101/",
    # Сохраняем название организации.
    "name": "Пример",
    # Сохраняем координаты в порядке долгота, широта.
    "coordinates_lon_lat": [37.105, 55.105],
    # Сохраняем фактические названия рубрик.
    "rubrics": ["Кафе"],
    # Сохраняем категории, по которым обнаружена организация.
    "found_by": ["cafe"],
}


# Пишем минимальный манифест выбранной территории.
def manifest(folder, organizations=None):
    # Создаём папку до сохранения её состава.
    folder.mkdir(parents=True, exist_ok=True)
    # По умолчанию включаем одну вымышленную организацию.
    rows = [ORGANIZATION] if organizations is None else organizations
    # Оставляем видимый охват поиска и его отдельный статус.
    data = {
        # Сохраняем явное ограничение охвата видимой выдачей.
        "coverage": "visible_search_results_only",
        # Сохраняем подтверждённые организации внутри области.
        "organizations": rows,
        # Сохраняем поле searches.
        "searches": [{"key": "cafe", "status": "visible_results_exhausted"}],
    }
    # Создаём обычный вход для публичной загрузки таблиц.
    (folder / "organizations.json").write_text(json.dumps(data), encoding="utf-8")


# Формируем полный текущий отзыв или запись из прежнего посещения.
def review(number, observation="dom"):
    # Прежняя запись содержит текст, но не подтверждена текущей страницей.
    return {
        # Сохраняем вымышленный идентификатор проверки.
        "id": str(number),
        # Сохраняем поле author.
        "author": "Автор",
        # Сохраняем поле date.
        "date": "2026-10-01T12:00:00+03:00",
        # Сохраняем поисковый запрос.
        "text": "Полный текст",
        # Сохраняем поле rating.
        "rating": 4,
        # Сохраняем поле observation.
        "observation": observation,
        # Сохраняем поле full_text_confirmed.
        "full_text_confirmed": observation == "dom",
        # Сохраняем поле text_truncated.
        "text_truncated": False,
    }


# Сохраняем отзывный снимок с отдельной общей оценкой организации.
def snapshot(folder, rows, status="stalled", suffix=""):
    # Снимки отзывов отделены от результатов поиска.
    (folder / "reviews").mkdir(exist_ok=True)
    # Общая оценка 4,7 не должна заменяться средним звёзд в небольшой выборке.
    data = {
        # Сохраняем идентификатор организации Яндекса.
        "organization_id": "101",
        # Сохраняем состояние поиска.
        "status": status,
        # Сохраняем время начала попытки.
        "collected_at": "2026-10-03T00:00:00Z",
        # Сохраняем поле company.
        "company": {"rating": 4.7, "ratings_count": 12, "reviews_count": 9},
        # Сохраняем поле observed_in_dom.
        "observed_in_dom": 1,
        # Сохраняем поле confirmed_full_texts.
        "confirmed_full_texts": 1,
        # Сохраняем список отзывов.
        "reviews": rows,
        # Сохраняем поле completeness.
        "completeness": {"retained_from_previous": len(rows) - 1},
    }
    # Имя файла различает полный снимок и заданный лимит.
    (folder / "reviews" / f"101{suffix}.json").write_text(json.dumps(data), encoding="utf-8")


# Проверяем поведение интерфейса на данных, а не только состав вызова.
class PackageTests(unittest.TestCase):
    # GeoJSON объединяет каждую организацию только с её отзывами без потери точности.
    def test_geojson_groups_dates_and_missing_values(self):
        # Изолируем вымышленные данные от реальных снимков.
        with tempfile.TemporaryDirectory() as temporary:
            # Подготавливаем состав из двух организаций.
            folder = Path(temporary)
            # У первой организации точные координаты, у второй положение неизвестно.
            manifest(
                # У первой организации точные координаты, у второй положение неизвестно.
                folder,
                [
                    # У первой организации точные координаты, у второй положение неизвестно.
                    dict(
                        # У первой организации точные координаты, у второй положение неизвестно.
                        ORGANIZATION, coordinates_lon_lat=[37.123456789012344, 55.123456789012344]
                    ),
                    # У первой организации точные координаты, у второй положение неизвестно.
                    dict(ORGANIZATION, organization_id="102", coordinates_lon_lat=None),
                ],
            )
            # Сохраняем текущий и прежний отзывы первой организации.
            snapshot(folder, [review(1), review(2, "previous_snapshot")])
            # Вторая организация пока не имеет отзывного снимка.
            result = load_results(folder)
            # Проверяем экспорт без установки или импорта GeoPandas.
            with patch.dict("sys.modules", {"geopandas": None}):
                # Возвращаем обычный словарь, совместимый с JSON.
                data = result.to_geojson()
                # Атомарно сохраняем коллекцию на диск.
                path = result.save_geojson(folder / "export" / "organizations.geojson")
            # Объектов столько же, сколько организаций, включая организацию без отзывов.
            self.assertEqual(len(data["features"]), 2)
            # Геометрия хранит точные координаты без округления Pandas JSON.
            self.assertEqual(
                # Геометрия хранит точные координаты без округления Pandas JSON.
                data["features"][0]["geometry"]["coordinates"],
                # Геометрия хранит точные координаты без округления Pandas JSON.
                [37.123456789012344, 55.123456789012344],
            )
            # Не подменяем неизвестное положение центром выбранной области.
            self.assertIsNone(data["features"][1]["geometry"])
            # Отзывы первой организации не попадают к соседнему объекту.
            self.assertEqual(data["features"][1]["properties"]["reviews"], [])
            # Старые записи сохраняются с явным признаком неподтверждённости.
            self.assertFalse(data["features"][0]["properties"]["reviews"][1]["confirmed"])
            # Дата сериализуется в UTC и остаётся связанной с исходной строкой.
            self.assertEqual(
                # Дата сериализуется в UTC и остаётся связанной с исходной строкой.
                data["features"][0]["properties"]["reviews"][0]["date"],
                # Дата сериализуется в UTC и остаётся связанной с исходной строкой.
                "2026-10-01T09:00:00+00:00",
            )
            # Отсутствующий идентификатор исходного отзыва должен стать null.
            self.assertIsNone(data["features"][0]["properties"]["reviews"][0]["source_review_id"])
            # Записанный файл строго соответствует возвращённому GeoJSON.
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), data)
            # В исходных таблицах сохраняются дата Pandas и исходные столбцы координат.
            self.assertIn("longitude", result.organizations)
            # Экспорт не меняет дату в вызывающем коде.
            self.assertEqual(result.reviews.loc[0, "date"].hour, 9)

    # Фильтрация не меняет состояние исходного снимка и не теряет организации.
    def test_geojson_confirmed_filter_and_empty_result(self):
        # Подготавливаем частичный снимок с двумя сохранёнными отзывами.
        with tempfile.TemporaryDirectory() as temporary:
            # Создаём манифест и отзывы.
            folder = Path(temporary)
            # Записываем одну организацию.
            manifest(folder)
            # Одна строка подтверждена, другая сохранена только из прошлого снимка.
            snapshot(folder, [review(1), review(2, "previous_snapshot")])
            # Загружаем исходные данные.
            result = load_results(folder)
            # Выгружаем только подтверждённые полные записи.
            filtered = result.to_geojson(confirmed_only=True)
            # К объекту относится лишь текущий отзыв.
            properties = filtered["features"][0]["properties"]
            # Размер экспорта указан отдельно от размера исходного снимка.
            self.assertEqual(properties["exported_reviews"], 1)
            # Исходный счётчик сохранённых строк не подменяется фильтрованным.
            self.assertEqual(properties["saved_reviews"], 2)
            # Частичный снимок остаётся частичным после фильтрации.
            self.assertEqual(properties["review_status"], "stalled")
            # В свойствах остаётся исходная диагностика полноты.
            self.assertEqual(properties["completeness"]["retained_from_previous"], 1)
            # Признак фильтрации доступен на уровне коллекции.
            self.assertTrue(filtered["metadata"]["confirmed_only"])
            # Неправильный тип фильтра не принимается молча.
            with self.assertRaises(ValueError):
                # Строка False не является логическим False.
                result.to_geojson(confirmed_only="False")
            # Пустой поиск даёт корректную пустую коллекцию.
            manifest(folder, [])
            # Все столбцы сохраняются, но объектов нет.
            self.assertEqual(load_results(folder).to_geojson()["features"], [])

    # Неудачный экспорт не должен затирать уже записанный GeoJSON.
    def test_geojson_atomic_failure_preserves_previous_file(self):
        # Проверяем запись в независимой папке.
        with tempfile.TemporaryDirectory() as temporary:
            # Создаём корректный исходный снимок.
            folder = Path(temporary)
            # Записываем манифест.
            manifest(folder)
            # Загружаем организацию без отзывов.
            result = load_results(folder, with_reviews=False)
            # Создаём прежний корректный файл экспорта.
            target = result.save_geojson(folder / "organizations.geojson")
            # Сохраняем точные байты для проверки отсутствия повреждения.
            original = target.read_bytes()
            # Подставляем ошибочную координату в изменяемой таблице пользователя.
            result.organizations.loc[0, "longitude"] = float("inf")
            # Ошибка должна возникнуть до замены файла.
            with self.assertRaises(ValueError):
                # Пытаемся обновить файл повреждённой геометрией.
                result.save_geojson(target)
            # Прежний экспорт остаётся без изменений.
            self.assertEqual(target.read_bytes(), original)
            # Проверяем также отказ сериализации произвольного пользовательского свойства.
            result.organizations.loc[0, "longitude"] = 37.105
            # Значения такого типа не поддерживаются JSON.
            result.metadata["review_runs"]["101"]["completeness"] = {"invalid": object()}
            # Атомарная запись должна оставить старый файл при ошибке JSON.
            with self.assertRaises(TypeError):
                # Запись временного файла не заменяет прежний результат при отказе.
                result.save_geojson(target)
            # Ни одна ошибка не повреждает уже готовый файл.
            self.assertEqual(target.read_bytes(), original)

    # Полный справочник сохраняет прежние ключи и все опубликованные рубрики.
    def test_full_catalog_and_aliases(self):
        # Проверяем единственный источник категорий для карты и Python.
        catalog = list_categories()
        # Содержим 20 готовых настроек и 1589 опубликованных рубрик.
        self.assertEqual(len(catalog), 1609)
        # Каждая настройка имеет явно проверяемую рубрику.
        self.assertTrue(
            # Каждая настройка имеет явно проверяемую рубрику.
            all(item["label"] and item["query"] and item["rubrics"] for item in catalog.values())
        )
        # Запускаем существующий сценарий только для чтения справочника.
        from discover_yandex import resolve_searches

        # Прежнее русское название продолжает разрешаться в прежний короткий ключ.
        self.assertEqual(resolve_searches(["Кафе"], [])[0]["key"], "cafe")
        # Рубрика недвижимости имеет собственный ключ и точное название.
        self.assertEqual(catalog["rubric_13_3_7"]["label"], "Жилой комплекс")
        # Раздел объектов Яндекс Бизнеса включён отдельно.
        self.assertEqual(catalog["business_1"]["label"], "Комплекс апартаментов")

    # Поиск по большому списку сохраняет категории, скрытые текущим фильтром.
    def test_browser_category_filter_preserves_selection(self):
        # Разрешаем файлы относительно теста, а не текущей рабочей папки.
        root = Path(__file__).parent
        # Используем реальный код загрузки и фильтрации категорий.
        script = (
            # Используем реальный код загрузки и фильтрации категорий.
            (root / "area_selection.js")
            # Используем реальный код загрузки и фильтрации категорий.
            .read_text(encoding="utf-8")
            # Используем реальный код загрузки и фильтрации категорий.
            .split("fetch('categories.json')", 1)[1]
        )
        # Вся проверка выполняется на подставленных локальных файлах.
        with sync_playwright() as playwright:
            # Поддерживаем установленный Chromium или стандартный браузер Playwright.
            browser = playwright.chromium.launch(
                # Поддерживаем установленный Chromium или стандартный браузер Playwright.
                **(
                    # Поддерживаем установленный Chromium или стандартный браузер Playwright.
                    {"executable_path": os.environ["REVIEW_CHROMIUM_PATH"]}
                    # Поддерживаем установленный Chromium или стандартный браузер Playwright.
                    if os.environ.get("REVIEW_CHROMIUM_PATH")
                    # Если файла нет, начинаем без сохранённого результата.
                    else {}
                )
            )
            # Закрываем браузер также при неудачной проверке.
            try:
                # Создаём одну вкладку без открытий Яндекса.
                page = browser.new_page()
                # Подставляем HTML без зависимостей от CDN или картографической подложки.
                page.route(
                    # Подставляем HTML без зависимостей от CDN или картографической подложки.
                    "https://example.test/",
                    # Подставляем HTML без зависимостей от CDN или картографической подложки.
                    lambda route: route.fulfill(
                        # Браузер должен разобрать подставленное содержимое как страницу HTML.
                        content_type="text/html",
                        # Подставляем HTML без зависимостей от CDN или картографической подложки.
                        body='<input id="category-filter"><div id="categories"></div><div id="status"></div>',
                    ),
                )
                # Читаем тот же справочник, который установлен в пакете.
                page.route(
                    # Читаем тот же справочник, который установлен в пакете.
                    "https://example.test/categories.json",
                    # Читаем тот же справочник, который установлен в пакете.
                    lambda route: route.fulfill(
                        # Читаем тот же справочник, который установлен в пакете.
                        content_type="application/json",
                        # Читаем тот же справочник, который установлен в пакете.
                        body=(root / "categories.json").read_text(encoding="utf-8"),
                    ),
                )
                # Открываем только подставленную страницу.
                page.goto("https://example.test/")
                # Заменяем лишь внешнее обновление готовности экспорта.
                page.add_script_tag(
                    # Заменяем лишь внешнее обновление готовности экспорта.
                    content="const update=()=>{}; const status=document.getElementById('status'); fetch('categories.json')"
                    # Заменяем лишь внешнее обновление готовности экспорта.
                    + script
                )
                # Дожидаемся полного списка, не ограничивая его несколькими примерами.
                page.wait_for_function(
                    # Дожидаемся полного списка, не ограничивая его несколькими примерами.
                    "document.querySelectorAll('#categories input').length===1609"
                )
                # Фильтруем по категории жилой недвижимости.
                page.locator("#category-filter").fill("жилой комплекс")
                # Опубликованная рубрика остаётся доступной для выбора.
                page.locator('input[value="rubric_13_3_7"]').check()
                # Переключаем фильтр, скрывая выбранную строку.
                page.locator("#category-filter").fill("аптека")
                # Скрытие не должно снимать ранее выбранный флажок.
                self.assertTrue(page.locator('input[value="rubric_13_3_7"]').is_checked())
                # При пустом поиске все рубрики снова отображаются.
                page.locator("#category-filter").fill("")
                # Публикуем весь список без искусственного ограничения в интерфейсе.
                self.assertEqual(page.locator("#categories label:visible").count(), 1609)
            # Освобождаем единственный браузер проверки.
            finally:
                # Не оставляем фоновые процессы после выполнения теста.
                browser.close()

    # Частичный снимок сохраняет обе записи, но подтверждена лишь текущая.
    def test_partial_tables_and_provenance(self):
        # Файлы каждой проверки изолированы от реальных результатов.
        with tempfile.TemporaryDirectory() as temporary:
            # Подготавливаем манифест и две записи разного происхождения.
            folder = Path(temporary)
            # Записываем одну найденную организацию.
            manifest(folder)
            # Одна запись подтверждена, другая сохранена только из прошлого снимка.
            snapshot(folder, [review(1), review(2, "previous_snapshot")])
            # Загружаем обе связанные таблицы без браузера.
            result = load_results(folder)
            # Сохраняем обе записи, не смешивая подтверждение с наличием текста.
            self.assertEqual(result.reviews["confirmed"].tolist(), [True, False])
            # Фильтр возвращает только актуально подтверждённую запись.
            self.assertEqual(result.confirmed_reviews()["id"].tolist(), ["1"])
            # Идентификатор связывает две таблицы без изменения типа.
            self.assertEqual(str(result.reviews["organization_id"].dtype), "string")
            # Приводим часовой пояс даты к UTC.
            self.assertEqual(result.reviews.loc[0, "date"].hour, 9)
            # Общая оценка берётся из карточки организации.
            self.assertEqual(result.organizations.loc[0, "rating"], 4.7)
            # Расхождение со счётчиком сохраняется в доступных полях.
            self.assertEqual(result.organizations.loc[0, "observed_reviews"], 1)
            # Частичный статус не превращается в готовую выборку.
            self.assertFalse(result.organizations.loc[0, "reviews_ready"])
            # Подробная диагностика не дублирует все тексты отзывов.
            self.assertNotIn("reviews", result.metadata["review_runs"]["101"])

    # Пустая территория сохраняет типы и набор столбцов.
    def test_empty_result_and_missing_geometry(self):
        # Используем отдельный манифест без найденных организаций.
        with tempfile.TemporaryDirectory() as temporary:
            # Записываем корректный пустой поиск.
            folder = Path(temporary)
            # Пустота результата отличается от отсутствия файла.
            manifest(folder, [])
            # Создаём таблицы по одной и той же схеме.
            result = load_results(folder)
            # Фильтр должен работать и без единой строки.
            self.assertTrue(result.confirmed_reviews().empty)
            # Пространственная таблица сохраняет систему координат.
            self.assertEqual(result.to_geopandas().crs.to_epsg(), 4326)
            # Добавляем организацию с неизвестным положением между двумя известными.
            manifest(
                # Добавляем организацию с неизвестным положением между двумя известными.
                folder,
                [
                    # Добавляем организацию с неизвестным положением между двумя известными.
                    ORGANIZATION,
                    # Добавляем организацию с неизвестным положением между двумя известными.
                    dict(ORGANIZATION, organization_id="102", coordinates_lon_lat=None),
                    # Добавляем организацию с неизвестным положением между двумя известными.
                    dict(ORGANIZATION, organization_id="103"),
                ],
            )
            # Не подгружаем отзывные файлы для этой проверки карты.
            geo = load_results(folder, with_reviews=False).to_geopandas()
            # Пропуск не смещает точки соседних строк.
            self.assertEqual(geo.geometry.isna().tolist(), [False, True, False])
            # Координаты не переставлены местами.
            self.assertEqual(geo.geometry.iloc[2].x, 37.105)

    # Ограниченный файл не подменяется полным снимком и наоборот.
    def test_limit_and_search_without_reviews(self):
        # Сохраняем два файла одной карточки с разным назначением.
        with tempfile.TemporaryDirectory() as temporary:
            # Подготавливаем состав организаций.
            folder = Path(temporary)
            # Записываем манифест.
            manifest(folder)
            # Полный снимок пока не завершён.
            snapshot(folder, [review(1), review(2)], "stalled")
            # Ограниченная выборка подтверждена отдельно.
            snapshot(folder, [review(1)], "capped", ".limit1")
            # Загружаем именно заданный лимит.
            limited = load_results(folder, max_reviews=1)
            # Возвращаем только соответствующий файл.
            self.assertEqual(len(limited.reviews), 1)
            # Подтверждённая ограниченная выборка готова для её собственного лимита.
            self.assertTrue(limited.organizations.loc[0, "reviews_ready"])
            # Поиск без отзывов не подмешивает оба старых файла.
            result = load_results(folder, with_reviews=False)
            # Таблица отзывов остаётся пустой.
            self.assertTrue(result.reviews.empty)
            # Статус поясняет, почему отзывов нет.
            self.assertEqual(result.organizations.loc[0, "review_status"], "not_collected")

    # Все способы ввода территории используют один совместимый конфиг.
    def test_area_inputs_and_validation(self):
        # Проверяем каталог без сетевого обращения.
        self.assertIn("residential", list_categories())
        # Город возвращает точную геометрию и дату снимка.
        city, info = _area_config("Москва", None, None, None, ["residential"], None)
        # Геометрия прочитана из локального каталога.
        self.assertIn("geometry", city)
        # Дата границы не теряется при выборе по имени.
        self.assertEqual(info["snapshot"], list_cities()[0]["snapshot"])
        # Конфиг карты сохраняет её категории и свободные запросы.
        config = {"bbox": BBOX, "categories": ["cafe"], "queries": ["дом культуры"]}
        # Передача словаря не изменяет исходный объект.
        resolved, _ = _area_config(None, None, None, config, ["school"], None)
        # Явная категория заменяет выбор карты.
        self.assertEqual(resolved["categories"], ["school"])
        # Исходный словарь остаётся прежним.
        self.assertEqual(config["categories"], ["cafe"])
        # Несовместимые параметры отклоняются до процесса сбора.
        with patch("yandex_maps_reviews.api.subprocess.run") as run:
            # Не выбираем между городом и прямоугольником молча.
            with self.assertRaises(ValueError):
                # Передаём два противоречивых источника территории.
                collect_area(city="Москва", bbox=BBOX, tags=["cafe"])
            # Лимит не может быть логическим значением вместо числа.
            with self.assertRaises(ValueError):
                # True не должен означать один отзыв.
                collect_area(bbox=BBOX, tags=["cafe"], max_reviews=True)
            # Ошибочные параметры не открывают браузер.
            run.assert_not_called()

    # Частичный исход возвращает данные, а ошибка процесса не возвращает старый манифест.
    def test_partial_process_and_fatal_error(self):
        # Подготавливаем записанные промежуточные данные.
        with tempfile.TemporaryDirectory() as temporary:
            # Создаём папку результата.
            folder = Path(temporary)
            # Записываем состав организаций.
            manifest(folder)
            # Сохраняем незавершённый отзывный снимок.
            snapshot(folder, [review(1)])
            # Код 1 без stderr соответствует явному частичному исходу сценария.
            with patch(
                # Код 1 без stderr соответствует явному частичному исходу сценария.
                "yandex_maps_reviews.api.subprocess.run",
                # Код 1 без stderr соответствует явному частичному исходу сценария.
                return_value=subprocess.CompletedProcess([], 1, stderr=""),
                # Код 1 без stderr соответствует явному частичному исходу сценария.
            ) as run:
                # Вызываем публичную функцию с прямоугольником.
                result = collect_area(bbox=BBOX, tags=["cafe"], output=folder)
                # Передаём частичность отдельно от самих таблиц.
                self.assertEqual(result.metadata["run_status"], "partial")
                # Запускаем процесс без shell.
                self.assertNotIn("shell", run.call_args.kwargs)
            # Фатальная ошибка не должна выдать предыдущие файлы за новый запуск.
            with patch(
                # Фатальная ошибка не должна выдать предыдущие файлы за новый запуск.
                "yandex_maps_reviews.api.subprocess.run",
                # Фатальная ошибка не должна выдать предыдущие файлы за новый запуск.
                return_value=subprocess.CompletedProcess([], 1, stderr="Папка занята"),
            ):
                # Возвращаем понятное исключение Python.
                with self.assertRaisesRegex(RuntimeError, "Папка занята"):
                    # Существующий манифест не скрывает ошибку.
                    collect_area(bbox=BBOX, tags=["cafe"], output=folder)

    # Проверяем настоящий запуск сценария через импорт на готовом поисковом кэше.
    def test_real_cached_subprocess(self):
        # Готовый снимок исключает браузер и любые сетевые запросы.
        with tempfile.TemporaryDirectory() as temporary:
            # Выбираем независимую папку кэша.
            folder = Path(temporary)
            # Воспроизводим существующий формат ключа поиска.
            selection = dict(list_categories()["cafe"], key="cafe")
            # Ключ соответствует прямоугольнику, категории и лимиту.
            digest = hashlib.sha256(
                # Ключ соответствует прямоугольнику, категории и лимиту.
                json.dumps([BBOX, selection, 1], sort_keys=True, ensure_ascii=False).encode()
                # Ключ соответствует прямоугольнику, категории и лимиту.
            ).hexdigest()[:16]
            # Папка совпадает со структурой обычного результата.
            (folder / "search").mkdir()
            # Поисковый снимок уже содержит допустимый максимум организаций.
            data = {
                # Сохраняем версию формата снимка.
                "schema_version": 1,
                # Сохраняем границы выбранной области.
                "bbox": BBOX,
                # Сохраняем поле geometry.
                "geometry": None,
                # Сохраняем параметры поискового задания.
                "selection": selection,
                # Сохраняем максимум организаций на запрос.
                "max_orgs": 1,
                # Сохраняем состояние поиска.
                "status": "capped",
                # Сохраняем подтверждённые организации внутри области.
                "organizations": [ORGANIZATION],
                # Сохраняем кандидатов с причинами отбора.
                "candidates": [],
            }
            # Сохраняем снимок с именем, которое найдёт реальный сценарий.
            (folder / "search" / f"{digest}.json").write_text(json.dumps(data), encoding="utf-8")
            # Вызываем публичную функцию без подмены процесса и браузерных функций.
            result = collect_area(
                # Вызываем публичную функцию без подмены процесса и браузерных функций.
                bbox=BBOX,
                # Вызываем публичную функцию без подмены процесса и браузерных функций.
                tags=["cafe"],
                # Вызываем публичную функцию без подмены процесса и браузерных функций.
                max_orgs=1,
                # Вызываем публичную функцию без подмены процесса и браузерных функций.
                with_reviews=False,
                # Вызываем публичную функцию без подмены процесса и браузерных функций.
                output=folder,
            )
            # Реальный процесс завершился по ветви готового кэша.
            self.assertEqual(result.metadata["returncode"], 0)
            # Организация получена из существующего поиска.
            self.assertEqual(result.organizations["organization_id"].tolist(), ["101"])


# Позволяем запускать проверки отдельным файлом или через unittest.
if __name__ == "__main__":
    # Возвращаем обычный отчёт об автономных проверках.
    unittest.main()
