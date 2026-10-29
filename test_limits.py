"""Проверки полного сбора, свежих выборок и независимого кэша лимитов."""

# Проверяем параметры запуска без сетевых обращений.
import sys

# Создаём автономные сценарии стандартной библиотекой.
import unittest

# Изолируем контрольные снимки.
from tempfile import TemporaryDirectory

# Формируем пути результатов.
from pathlib import Path

# Подменяем ожидания и создание браузера.
from unittest.mock import patch

# Имитируем отсутствие доступа к сортировке.
from playwright.sync_api import TimeoutError as BrowserTimeout

# Проверяем сбор и локальные выборки.
from collect_yandex import EXTRACT, cache_complete, collect, load_review_cache, review_target

# Проверяем оба интерфейса запуска.
from collect_yandex import main as review_main

# Проверяем передачу лимита географическим поиском.
from discover_yandex import main as discovery_main

# Записываем обычные контрольные снимки.
from storage import save_result

# Повторно используем автономную страницу.
from test_reviews import OID, URL, example_html, fake_page


# Создаём полный отзыв с различающимися датами.
def row(index):
    # Поля позволяют проверить состав выборки без догадок по тексту.
    return {
        # Сохраняем вымышленный идентификатор проверки.
        "id": str(index),
        # Сохраняем поле author.
        "author": str(index),
        # Сохраняем поле author_url.
        "author_url": f"https://yandex.ru/maps/user/{index}",
        # Сохраняем поле date.
        "date": f"2026-09-{index:02d}",
        # Сохраняем поисковый запрос.
        "text": f"Текст {index}",
        # Сохраняем поле rating.
        "rating": 5,
        # Сохраняем поле text_truncated.
        "text_truncated": False,
    }


# Проверяем контракт полного сбора и ограничения на каждую организацию.
class LimitTests(unittest.TestCase):
    # Полный сбор не должен останавливаться после 25 прокруток или 240 секунд.
    def test_unlimited_beyond_previous_budgets(self):
        # Добавляем по одному отзыву за проход.
        visible = [1]
        # Первая встроенная порция пуста, но счётчик известен.
        page = fake_page(example_html(0, 30), {})
        # Возвращаем увеличивающуюся порцию DOM с полными текстами.
        page.evaluate = (
            # Возвращаем увеличивающуюся порцию DOM с полными текстами.
            lambda expression, arg: {
                # Сохраняем поле company.
                "company": {"reviews_count": 30},
                # Сохраняем список отзывов.
                "reviews": [row(i) for i in range(1, visible[0] + 1)],
            }
            # Возвращаем увеличивающуюся порцию DOM с полными текстами.
            if expression == EXTRACT
            # Возвращаем увеличивающуюся порцию DOM с полными текстами.
            else 0
        )
        # Счётчик DOM увеличивается только после ожидания новой порции.
        page.locator = lambda selector: type(
            # Счётчик DOM увеличивается только после ожидания новой порции.
            "Locator",
            (),
            # Счётчик DOM увеличивается только после ожидания новой порции.
            {"inner_text": lambda self, **kw: "Организация", "count": lambda self: visible[0]},
        )()
        # Не тратим реальное время на вымышленные задержки.
        page.wait_for_timeout = lambda milliseconds: None
        # Каждый проход действительно приносит новую запись.
        page.wait_for_function = lambda *args, **kwargs: visible.__setitem__(0, visible[0] + 1)
        # Искусственный таймер превышает прежний бюджет, не задерживая тест.
        ticks = iter(range(0, 100000, 20))
        # Не затрагиваем настоящие файлы отзывов.
        with TemporaryDirectory() as folder, patch(
            # Не затрагиваем настоящие файлы отзывов.
            "collect_yandex.time.monotonic",
            # Не затрагиваем настоящие файлы отзывов.
            side_effect=lambda: next(ticks),
        ):
            # Отсутствие лимита передаём как None.
            result = collect(page, OID, URL, Path(folder) / "all.json", None, 0)
            # Все тридцать отзывов собраны после двадцати девяти прокруток.
            self.assertEqual(
                # Все тридцать отзывов собраны после двадцати девяти прокруток.
                (result["status"], len(result["reviews"]), result["scrolls"]),
                # Все тридцать отзывов собраны после двадцати девяти прокруток.
                ("complete", 30, 29),
            )
            # Даты итогового полного снимка упорядочены от свежих к старым.
            self.assertEqual(result["reviews"][0]["date"], "2026-09-30")
            # Полный кэш пригоден для повторного сбора всех отзывов.
            self.assertTrue(cache_complete(result, OID, None))

    # Полный снимок позволяет получить свежую выборку без посещения сайта.
    def test_full_cache_projection_preserves_original(self):
        # Для каждого объёма используется отдельный файл.
        with TemporaryDirectory() as folder:
            # Сохраняем полный снимок в специально перемешанном порядке.
            target = review_target(folder, OID, None)
            # Все три записи подтверждены счётчиком карточки.
            full = {
                # Сохраняем версию формата снимка.
                "schema_version": 1,
                # Сохраняем идентификатор организации Яндекса.
                "organization_id": OID,
                # Сохраняем состояние поиска.
                "status": "complete",
                # Сохраняем поле company.
                "company": {"reviews_count": 3},
                # Сохраняем список отзывов.
                "reviews": [row(1), row(3), row(2)],
            }
            # Полный файл должен остаться побитно неизменным.
            save_result(target, full)
            # Запоминаем байты до создания ограниченной выборки.
            original = target.read_bytes()
            # Выбираем два самых свежих отзыва локально.
            limited = load_review_cache(review_target(folder, OID, 2), OID, 2)
            # Состав соответствует свежим датам, а не исходному порядку.
            self.assertEqual([r["date"] for r in limited["reviews"]], ["2026-09-03", "2026-09-02"])
            # Ограниченная выборка не выдаётся за полный сбор.
            self.assertFalse(cache_complete(limited, OID, None))
            # Своя ограниченная выборка пригодна для повторного запуска.
            self.assertTrue(cache_complete(limited, OID, 2))
            # Если отзывов меньше лимита, возвращаем все имеющиеся.
            smaller = load_review_cache(review_target(folder, OID, 50), OID, 50)
            # Пятьдесят — максимум, а не требуемое число строк.
            self.assertEqual((smaller["status"], len(smaller["reviews"])), ("complete", 3))
            # Сбор всех отзывов по-прежнему использует исходный файл.
            self.assertEqual(target.read_bytes(), original)

    # Прежняя популярная выборка не подтверждает самые свежие отзывы.
    def test_legacy_capped_cache_is_not_newest(self):
        # Имитируем прежний ограниченный снимок без сведений о сортировке.
        cached = {
            # Сохраняем идентификатор организации Яндекса.
            "organization_id": OID,
            # Сохраняем состояние поиска.
            "status": "capped",
            # Сохраняем поле company.
            "company": {"reviews_count": 10},
            # Сохраняем список отзывов.
            "reviews": [row(1), row(2)],
        }
        # Такой файл не подходит ни для свежей выборки, ни для полного сбора.
        self.assertFalse(cache_complete(cached, OID, 2))
        # Полноту нельзя подтвердить ограниченным набором.
        self.assertFalse(cache_complete(cached, OID, None))

    # Неподтверждённая сортировка должна иметь отдельный статус.
    def test_sort_failure_is_explicit(self):
        # Подготавливаем страницу с числом отзывов больше лимита.
        page = fake_page(example_html(2, 10), {})
        # Ошибку управления страницей воспроизводим без обращения к сайту.
        with TemporaryDirectory() as folder, patch(
            # Ошибку управления страницей воспроизводим без обращения к сайту.
            "collect_yandex.select_newest",
            # Ошибку управления страницей воспроизводим без обращения к сайту.
            side_effect=BrowserTimeout("sort unavailable"),
        ):
            # Сохраняем только требуемый максимум прочитанных текстов.
            result = collect(page, OID, URL, Path(folder) / "limited.json", 1, 0)
            # Старая выдача не становится успешно собранной свежей выборкой.
            self.assertEqual(result["status"], "sort_unavailable")
            # Лимит соблюдается также в незавершённом снимке.
            self.assertLessEqual(len(result["reviews"]), 1)

    # Оба интерфейса принимают положительный лимит и отклоняют ноль до браузера.
    def test_invalid_limits_rejected_before_browser(self):
        # Каждая команда использует собственную временную папку.
        with TemporaryDirectory() as folder:
            # Проверяем одинаковое имя параметра обоих способов запуска.
            cases = [
                # Проверяем одинаковое имя параметра обоих способов запуска.
                (review_main, ["collect_yandex.py", OID, "--max-reviews", "0"]),
                (
                    # Проверяем одинаковое имя параметра обоих способов запуска.
                    discovery_main,
                    [
                        # Проверяем одинаковое имя параметра обоих способов запуска.
                        "discover_yandex.py",
                        # Задаём имя параметра командной строки.
                        "--bbox",
                        # Проверяем одинаковое имя параметра обоих способов запуска.
                        "38",
                        # Проверяем одинаковое имя параметра обоих способов запуска.
                        "55",
                        # Проверяем одинаковое имя параметра обоих способов запуска.
                        "39",
                        # Проверяем одинаковое имя параметра обоих способов запуска.
                        "56",
                        # Задаём имя параметра командной строки.
                        "--categories",
                        # Проверяем одинаковое имя параметра обоих способов запуска.
                        "cafe",
                        # Задаём имя параметра командной строки.
                        "--max-reviews",
                        # Проверяем одинаковое имя параметра обоих способов запуска.
                        "0",
                    ],
                ),
            ]
            # Ошибочный лимит не должен начинать сетевую работу.
            for main, args in cases:
                # Отключаем создание браузера и стандартную диагностику теста.
                with patch.object(sys, "argv", args + ["--output", folder]), patch(
                    # Отключаем создание браузера и стандартную диагностику теста.
                    "collect_yandex.sync_playwright"
                    # Отключаем создание браузера и стандартную диагностику теста.
                ) as browser, patch("discover_yandex.sync_playwright") as search_browser, patch(
                    # Отключаем создание браузера и стандартную диагностику теста.
                    "sys.stderr"
                ):
                    # Аргументы завершаются стандартным кодом ошибки.
                    with self.assertRaises(SystemExit):
                        # Выполняем настоящий разбор параметров CLI.
                        main()
                    # Сеть не требуется для отклонения лимита.
                    browser.assert_not_called()
                    # Географический интерфейс также не открывает браузер.
                    search_browser.assert_not_called()

    # Максимум пятьдесят не должен требовать пятидесяти записей от маленькой карточки.
    def test_fewer_than_limit_and_invalid_date(self):
        # Используем три подтверждённых полных отзыва.
        batch = {"company": {"reviews_count": 3}, "reviews": [row(1), row(2), row(3)]}
        # Встроенный список пуст, чтобы состав определялся только видимой выдачей.
        page = fake_page(example_html(0, 3), batch)
        # Для маленькой карточки переключение сортировки не требуется.
        with TemporaryDirectory() as folder, patch("collect_yandex.select_newest") as sorter:
            # Ограничение задаёт максимум, а не минимальный объём.
            result = collect(page, OID, URL, Path(folder) / "result.json", 50, 0)
            # Сохраняем все три записи и подтверждаем полный сбор.
            self.assertEqual((result["status"], len(result["reviews"])), ("complete", 3))
            # Лишнее действие интерфейса не выполнялось.
            sorter.assert_not_called()
            # Повреждённая строка даты не должна считаться заполненным полем.
            result["reviews"][0]["date"] = "не дата"
            # Неподтверждённая свежесть требует нового сбора.
            self.assertFalse(cache_complete(result, OID, 50))

    # Виртуализация заменяет карточки, сохраняя постоянное число DOM-узлов.
    def test_virtualized_window_collects_all(self):
        # В окне всегда пять карточек, общее число отзывов — тридцать.
        last = [5]
        # Начальная HTML-порция пуста, счётчик карточки известен.
        page = fake_page(example_html(0, 30), {})

        # Выполнение каждого сценария зависит от текущего окна, а не от его размера.
        def evaluate(expression, arg):
            # Возвращаем именно текущие пять отзывов.
            if expression == EXTRACT:
                # Новые окна перекрываются, поэтому ключи должны объединяться.
                return {
                    # Сохраняем поле company.
                    "company": {"reviews_count": 30},
                    # Сохраняем список отзывов.
                    "reviews": [row(i) for i in range(last[0] - 4, last[0] + 1)],
                }
            # Последний отзыв меняется при каждом успешном ожидании.
            from collect_yandex import TAIL

            # Размер окна остаётся постоянным и не может использоваться как признак прогресса.
            return str(last[0]) if expression == TAIL else 0

        # Используем управляемую выдачу без внешней сети.
        page.evaluate = evaluate
        # Подменяем только временное ожидание браузера.
        page.wait_for_timeout = lambda milliseconds: None
        # Следующая прокрутка заменяет один старый отзыв новым.
        page.wait_for_function = lambda *args, **kwargs: last.__setitem__(0, last[0] + 1)
        # Изолируем снимок виртуализированной страницы.
        with TemporaryDirectory() as folder:
            # Полный сбор должен пройти все окна независимо от постоянного размера DOM.
            result = collect(page, OID, URL, Path(folder) / "result.json", None, 0)
            # Итог содержит все тридцать уникальных отзывов.
            self.assertEqual((result["status"], len(result["reviews"])), ("complete", 30))
            # Каждое успешное ожидание продвинуло окно на один отзыв.
            self.assertEqual(result["scrolls"], 25)


# Разрешаем запуск файла отдельно.
if __name__ == "__main__":
    # Выполняем автономные проверки лимитов.
    unittest.main()
