"""Проверки подгрузки, текущих счётчиков и сохранения незавершённых заданий."""

# Читаем сохранённый журнал очереди.
import json

# Выбираем тот же браузер, что и в остальных автономных проверках.
import os

# Запускаем автономные проверки.
import unittest

# Работаем с временными путями результатов.
from pathlib import Path

# Не оставляем тестовые отзывы в репозитории.
from tempfile import TemporaryDirectory

# Подставляем метаданные браузерных событий.
from types import SimpleNamespace

# Управляем только внешними зависимостями сценариев.
from unittest.mock import patch

# Различаем истечение ожидания и появление новой карточки.
from playwright.sync_api import TimeoutError as BrowserTimeout, sync_playwright

# Проверяем основной путь сборщика.
from collect_yandex import (
    # Проверяем основной путь сборщика.
    collect,
    # Подключаем общий последовательный обработчик карточек отзывов.
    collect_many,
    # Проверяем основной путь сборщика.
    EXTRACT,
    # Проверяем основной путь сборщика.
    DOM_KEYS,
    # Проверяем основной путь сборщика.
    DOM_PROGRESS,
    # Подключаем проверку достаточности сохранённых отзывов.
    cache_complete,
    # Проверяем основной путь сборщика.
    review_key,
    # Проверяем основной путь сборщика.
    load_review_cache,
    # Проверяем основной путь сборщика.
    review_target,
)

# Проверяем самостоятельный поиск организаций.
from discover_yandex import discover, SCROLL_SEARCH, search_cache_complete

# Создаём сохранённый снимок для сценария обновления.
from storage import save_result

# Используем общие образцы полей отзывов.
from test_limits import row

# Используем небольшой локальный образец HTML.
from test_reviews import OID, URL, example_html, fake_page

# Используем общий образец поисковой выдачи.
from test_discovery import BBOX, CAFE, fixture_html, fake_search_page

# Проверяем геометрию тем же путём, что и основной поиск.
from area_geometry import load_boundary

# Создаём простой замкнутый контур.
from test_geometry import polygon


# Поддерживаем несколько обработчиков одного события, как настоящий браузер.
def events(page):
    # Храним подписки тестового сценария.
    handlers = {}
    # Регистрируем обработчики без перезаписи предыдущей подписки.
    page.on = lambda name, handler: handlers.setdefault(name, []).append(handler)
    # Симулируем успешное открытие без дополнительных ресурсов.
    page.goto = lambda *args, **kwargs: SimpleNamespace(status=200)

    # Отправляем событие всем подключённым наблюдателям.
    def emit(name, value):
        # Используем копию на случай изменения подписок при обработке.
        for handler in list(handlers.get(name, [])):
            # Передаём только метаданные тестового запроса.
            handler(value)

    # Возвращаем управляющий сценарий событий браузера.
    return emit


# Проверяем пограничные состояния сбора без обращения к Яндексу.
class PipelineTests(unittest.TestCase):
    # Последний таймаут не должен терять уже появившиеся в DOM записи.
    def test_final_timeout_reads_late_batch(self):
        # Первые две записи уже видны на странице.
        visible = [row(1), row(2)]
        # Текущий счётчик ожидает три отзыва.
        page = fake_page(example_html(0, 3), {})
        # Читаем текущую порцию при каждом проходе.
        page.evaluate = (
            # Читаем текущую порцию при каждом проходе.
            lambda expression, arg: {"company": {}, "reviews": visible.copy()}
            # Читаем текущую порцию при каждом проходе.
            if expression == EXTRACT
            # Читаем текущую порцию при каждом проходе.
            else 0
        )
        # Считаем завершённые ожидания, без реальных пауз.
        waits = [0]

        # Третий отзыв появляется непосредственно перед последним таймаутом.
        def wait(*args, **kwargs):
            # Учитываем одно ожидание подгрузки.
            waits[0] += 1
            # В последнюю порцию добавляется полноценный отзыв.
            if waits[0] == 3:
                # Сохраняем поля записи без сокращений.
                visible.append(row(3))
            # Уведомление может опоздать, хотя DOM уже изменился.
            raise BrowserTimeout("late notification")

        # Задаём контролируемую последовательность ожиданий.
        page.wait_for_function = wait
        # Изолируем результат сценария.
        with TemporaryDirectory() as folder:
            # Выполняем основной сбор без изменения его логики.
            result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
            # Все три записи должны сохраниться с подтверждённой полнотой.
            self.assertEqual((result["status"], result["valid_reviews"]), ("complete", 3))

    # Уведомления о перестановке известных карточек не дают бесконечный цикл.
    def test_reordering_does_not_reset_attempts(self):
        # Несколько видимых записей меньше счётчика источника.
        page = fake_page(example_html(0, 5), {"company": {}, "reviews": [row(1), row(2)]})
        # Отслеживаем все реальные значения пауз и ожиданий.
        page.wait_for_timeout = unittest.mock.Mock()
        # Уведомление срабатывает, но уникальных отзывов не прибавляется.
        page.wait_for_function = unittest.mock.Mock()
        # Не сохраняем тестовые данные в рабочую папку.
        with TemporaryDirectory() as folder:
            # Собираем до прежних трёх неудачных попыток.
            result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
            # Перестановка не подтверждает полноту и не сбрасывает счётчик.
            self.assertEqual((result["status"], result["scrolls"]), ("stalled", 3))
            # Основная пауза остаётся пять секунд на каждом проходе.
            self.assertEqual(
                # Основная пауза остаётся пять секунд на каждом проходе.
                sum(call.args == (5000,) for call in page.wait_for_timeout.call_args_list),
                # Основная пауза остаётся пять секунд на каждом проходе.
                3,
            )
            # Условное ожидание сохраняет прежний предел десять секунд.
            self.assertTrue(
                # Условное ожидание сохраняет прежний предел десять секунд.
                all(
                    # Условное ожидание сохраняет прежний предел десять секунд.
                    call.kwargs["timeout"] == 10000
                    # Условное ожидание сохраняет прежний предел десять секунд.
                    for call in page.wait_for_function.call_args_list
                )
            )

    # Незавершённая загрузка не должна порождать дополнительные прокрутки.
    def test_pending_load_is_bounded_and_not_rescrolled(self):
        # Видны два из трёх ожидаемых отзывов.
        page = fake_page(example_html(0, 3), {"company": {}, "reviews": [row(1), row(2)]})
        # Подключаем все наблюдатели браузера.
        emit = events(page)
        # Запрос относится к обычной загрузке страницы карт.
        request = SimpleNamespace(url=URL, resource_type="fetch")
        # Сохраняем исходное чтение DOM.
        evaluate = page.evaluate

        # Запускаем загрузку при первой прокрутке и оставляем её незавершённой.
        def scroll(expression, arg):
            # Имитация относится только к действию прокрутки.
            if "scrollIntoView" in expression:
                # Наблюдатель узнаёт о запросе из события браузера.
                emit("request", request)
            # Остальное поведение страницы остаётся прежним.
            return evaluate(expression, arg)

        # Подключаем управляемую загрузку.
        page.evaluate = scroll
        # Уведомления о новых карточках отсутствуют.
        page.wait_for_function = unittest.mock.Mock(side_effect=BrowserTimeout("pending"))
        # Изолируем файлы сценария.
        with TemporaryDirectory() as folder:
            # Выполняем сбор с неизменным пределом ожидания.
            result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
            # Три ожидания выполняются после одной прокрутки.
            self.assertEqual(
                # Три ожидания выполняются после одной прокрутки.
                (result["status"], result["scrolls"], page.wait_for_function.call_count),
                # Три ожидания выполняются после одной прокрутки.
                ("load_timeout", 1, 3),
            )
            # Диагностика сохраняет незавершённый запрос без его тела.
            self.assertEqual(result["network"]["pending_loads"], 1)

    # Завершение ранее начатого запроса может дать новую порцию без прокрутки.
    def test_pending_load_can_finish_without_rescroll(self):
        # Начинаем с двух отзывов.
        visible = [row(1), row(2)]
        # Счётчик ожидает третью запись.
        page = fake_page(example_html(0, 3), {})
        # Подключаем несколько наблюдателей событий.
        emit = events(page)
        # Используем один и тот же объект незавершённого запроса.
        request = SimpleNamespace(url=URL, resource_type="fetch")
        # Считаем ожидания после начала загрузки.
        waits = [0]

        # Возвращаем текущий DOM либо запускаем загрузку прокруткой.
        def evaluate(expression, arg):
            # Передаём полные текущие отзывы.
            if expression == EXTRACT:
                # Обновления списка не изменяют его прежние записи.
                return {"company": {}, "reviews": visible.copy()}
            # Начало подгрузки происходит только при прокрутке.
            if "scrollIntoView" in expression:
                # Запрос пока не завершён.
                emit("request", request)
            # Другие действия не меняют страницу.
            return 0

        # Запрос завершается во второе ожидание.
        def wait(*args, **kwargs):
            # Отсчитываем интервалы без реального ожидания.
            waits[0] += 1
            # Данные становятся доступны позднее первого интервала.
            if waits[0] == 2:
                # Браузер сообщил о завершении необходимой загрузки.
                emit("requestfinished", request)
                # Новая запись появляется в DOM.
                visible.append(row(3))
            # Проверяем также чтение после таймаута уведомления.
            raise BrowserTimeout("late")

        # Подключаем управляемые операции.
        page.evaluate = evaluate
        # Подключаем завершение запроса.
        page.wait_for_function = wait
        # Изолируем сохранение.
        with TemporaryDirectory() as folder:
            # Выполняем обычный сбор.
            result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
            # Завершившийся запрос не требует повторной прокрутки.
            self.assertEqual(
                # Завершившийся запрос не требует повторной прокрутки.
                (result["status"], result["scrolls"], result["network"]["pending_loads"]),
                # Завершившийся запрос не требует повторной прокрутки.
                ("complete", 1, 0),
            )

    # Отказы подгрузки не следует обозначать как обычный конец списка.
    def test_loading_failures_are_distinguished(self):
        # Проверяем обрыв сети и HTTP-ошибку сервера отдельно.
        for event, expected in [("requestfailed", "network_error"), ("response", "server_error")]:
            # Каждая причина получает самостоятельную диагностику.
            with self.subTest(event=event), TemporaryDirectory() as folder:
                # Несколько ожидаемых отзывов ещё отсутствуют.
                page = fake_page(example_html(0, 3), {"company": {}, "reviews": [row(1)]})
                # Подключаем все наблюдатели загрузки.
                emit = events(page)
                # Сетевой отказ имеет только метаданные.
                request = SimpleNamespace(
                    # Сетевой отказ имеет только метаданные.
                    url=URL,
                    # Сетевой отказ имеет только метаданные.
                    resource_type="fetch",
                    # Сетевой отказ имеет только метаданные.
                    failure="net::ERR_INTERNET_DISCONNECTED",
                )
                # Серверная ошибка также не содержит тела ответа.
                response = SimpleNamespace(url=URL, request=request, status=503, headers={})
                # Сохраняем чтение текущего DOM.
                evaluate = page.evaluate

                # Возвращаем выбранный отказ при подгрузке.
                def scroll(expression, arg):
                    # Отказ относится к необходимой загрузке страницы.
                    if "scrollIntoView" in expression:
                        # Отправляем только событие, которое проверяется этим случаем.
                        emit(event, request if event == "requestfailed" else response)
                    # Чтение записей остаётся доступным.
                    return evaluate(expression, arg)

                # Подключаем контролируемый отказ.
                page.evaluate = scroll
                # До конца интервала нет новых записей.
                page.wait_for_function = unittest.mock.Mock(side_effect=BrowserTimeout("no data"))
                # Выполняем обычную обработку.
                result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
                # Сохраняем частичные записи с точной причиной остановки.
                self.assertEqual((result["status"], result["valid_reviews"]), (expected, 1))

    # Текущий DOM может противоречить нулевому счётчику встроенного HTML.
    def test_zero_html_count_does_not_erase_dom_reviews(self):
        # HTML сообщает отсутствие отзывов, но запись видна на странице.
        page = fake_page(
            # HTML сообщает отсутствие отзывов, но запись видна на странице.
            example_html(0, 0),
            # HTML сообщает отсутствие отзывов, но запись видна на странице.
            {"company": {"reviews_count": 1}, "reviews": [row(1)]},
        )
        # Проверяем сохранение без рабочего кэша.
        with TemporaryDirectory() as folder:
            # Выполняем основной путь сверки HTML и DOM.
            result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
            # Противоречие не должно давать пустой успешный файл.
            self.assertEqual((result["status"], result["valid_reviews"]), ("count_mismatch", 1))

    # Неизвестный текущий счётчик не подтверждает отсутствие отзывов.
    def test_zero_html_requires_dom_confirmation(self):
        # На странице пока нет ни счётчика, ни видимых записей.
        page = fake_page(example_html(0, 0), {"company": {}, "reviews": []})
        # Ожидание подтверждения исчерпано.
        page.wait_for_function = unittest.mock.Mock(
            # Ожидание подтверждения исчерпано.
            side_effect=BrowserTimeout("missing DOM counter")
        )
        # Сохраняем прежний прогресс отдельно от рабочего кэша.
        with TemporaryDirectory() as folder:
            # Повторная попытка использует прежний путь.
            target = Path(folder) / "reviews.json"
            # Старый отзыв нельзя уничтожить по одному HTML-счётчику.
            save_result(
                # Старый отзыв нельзя уничтожить по одному HTML-счётчику.
                target,
                # Старый отзыв нельзя уничтожить по одному HTML-счётчику.
                {"organization_id": OID, "company": {}, "status": "stalled", "reviews": [row(1)]},
            )
            # Запускаем сверку с текущей страницей.
            result = collect(page, OID, URL, target, None, 5)
            # Незавершённая сверка сохраняет старую запись и явный таймаут.
            self.assertEqual((result["status"], len(result["reviews"])), ("timeout", 1))

    # Сбой диска не должен оставлять подписки предыдущей задачи.
    def test_save_failure_releases_page_handlers(self):
        # Проверяем оба потребителя общего наблюдателя загрузок.
        for kind in ["reviews", "search"]:
            # Изолируем каждый путь обработки.
            with self.subTest(kind=kind), TemporaryDirectory() as folder:
                # Создаём подходящий образец страницы.
                page = (
                    # Создаём подходящий образец страницы.
                    fake_page(example_html(0, 1), {"company": {}, "reviews": [row(1)]})
                    # Создаём подходящий образец страницы.
                    if kind == "reviews"
                    # Создаём подходящий образец страницы.
                    else fake_search_page(fixture_html(), [])
                )
                # Считаем снятые подписки.
                page.remove_listener = unittest.mock.Mock()
                # Проверяем снятие фильтра ресурсов.
                page.unroute = unittest.mock.Mock()
                # Выбираем функцию записи текущего потребителя.
                module = "collect_yandex" if kind == "reviews" else "discover_yandex"
                # Отказ сохранения должен выйти к вызывающей команде.
                with patch(module + ".save_result", side_effect=OSError("disk full")):
                    # Повторная финальная запись тоже может завершиться отказом.
                    with self.assertRaises(OSError):
                        # Выполняем соответствующий реальный путь обработки.
                        if kind == "reviews":
                            # Запускаем сбор одной карточки.
                            collect(page, OID, URL, Path(folder) / "result.json", None, 5)
                        # Второй путь использует тот же браузерный наблюдатель.
                        else:
                            # Запускаем поисковую задачу.
                            discover(page, BBOX, CAFE, Path(folder) / "result.json", 20, 5)
                # Все четыре подписки наблюдателя и обработчик ограничений сняты.
                self.assertEqual(page.remove_listener.call_count, 5)
                # Фильтр ресурсов также удалён.
                page.unroute.assert_called_once()

    # Изменение сортировки во время подгрузки не даёт готовую свежую выборку.
    def test_sort_change_during_loading_is_explicit(self):
        # В начале доступен один из двух требуемых отзывов.
        visible = [row(1)]
        # Всего отзывов больше лимита, поэтому сортировка обязательна.
        page = fake_page(example_html(0, 100), {})
        # Чтение сохраняет текущее состояние списка.
        page.evaluate = (
            # Чтение сохраняет текущее состояние списка.
            lambda expression, arg: {"company": {}, "reviews": [value.copy() for value in visible]}
            # Чтение сохраняет текущее состояние списка.
            if expression == EXTRACT
            # Чтение сохраняет текущее состояние списка.
            else 0
        )
        # После ожидания появляется ещё один отзыв.
        page.wait_for_function = lambda *args, **kwargs: visible.append(row(2))
        # Сохраняем обычную проверку текста страницы.
        locator = page.locator
        # В первом проходе сортировка верна, во втором подпись меняется.
        labels = iter(["По новизне", "По умолчанию"])
        # Подменяем только чтение подписи сортировки.
        page.locator = (
            # Подменяем только чтение подписи сортировки.
            lambda selector: SimpleNamespace(inner_text=lambda **kwargs: next(labels))
            # Подменяем только чтение подписи сортировки.
            if "ranking" in selector
            # Подменяем только чтение подписи сортировки.
            else locator(selector)
        )
        # Первоначальный выбор порядка считается успешным.
        with TemporaryDirectory() as folder, patch("collect_yandex.select_newest"):
            # Повторная подгрузка должна проверить сохранение порядка.
            result = collect(page, OID, URL, Path(folder) / "result.json", 2, 5)
            # Наличие двух записей не подтверждает их свежесть после изменения порядка.
            self.assertEqual((result["status"], len(result["reviews"])), ("sort_unavailable", 2))

    # Старый маленький счётчик не должен отключать сортировку свежей выборки.
    def test_sort_decision_uses_current_dom_count(self):
        # Создаём снимок карточки, у которой раньше было меньше отзывов.
        old = {
            # Сохраняем идентификатор организации Яндекса.
            "organization_id": OID,
            # Сохраняем состояние поиска.
            "status": "complete",
            # Сохраняем поле company.
            "company": {"reviews_count": 1},
            # Сохраняем список отзывов.
            "reviews": [row(1)],
        }
        # Текущий HTML не содержит встроенного состояния.
        page = fake_page("<html></html>", {"company": {"reviews_count": 100}, "reviews": [row(2)]})
        # Сортировка должна быть обязательна до принятия одного свежего отзыва.
        with TemporaryDirectory() as folder, patch(
            # Сортировка должна быть обязательна до принятия одного свежего отзыва.
            "collect_yandex.select_newest",
            # Сортировка должна быть обязательна до принятия одного свежего отзыва.
            side_effect=BrowserTimeout("sort unavailable")
            # Сортировка должна быть обязательна до принятия одного свежего отзыва.
        ) as sorter:
            # Записываем прежний прогресс.
            target = Path(folder) / "reviews.json"
            # Снимок проходит ту же проверку, что и обычный кэш.
            save_result(target, old)
            # Пытаемся получить ограниченную свежую выборку.
            result = collect(page, OID, URL, target, 1, 5)
            # Без сортировки свежесть не подтверждается.
            self.assertEqual(result["status"], "sort_unavailable")
            # Проверяем, что управление сортировкой действительно потребовалось.
            sorter.assert_called_once()

    # Полный сохранённый текст не мешает обновить оценку и получить отредактированный текст.
    def test_collapsed_review_updates_rating_and_edited_text(self):
        # Проверяем прежний текст и изменённое начало отдельно.
        for text, truncated in [("Начало…", True), ("Изменённый полный текст", False)]:
            # Изолируем версии одного отзыва.
            with self.subTest(text=text), TemporaryDirectory() as folder:
                # Сохранённая оценка отличается от текущей.
                previous = dict(row(1), text="Начало. Прежний полный текст.", rating=1)
                # Текущая запись сохраняет профиль и дату.
                current = dict(row(1), text=text, text_truncated=truncated, rating=5)
                # Карточка содержит один отзыв.
                page = fake_page(example_html(0, 1), {"company": {}, "reviews": [current]})
                # Сохраняем старый снимок для обновления.
                target = Path(folder) / "reviews.json"
                # Проверяем объединение с прежним полным текстом.
                save_result(
                    # Проверяем объединение с прежним полным текстом.
                    target,
                    {
                        # Сохраняем идентификатор организации Яндекса.
                        "organization_id": OID,
                        # Сохраняем список отзывов.
                        "reviews": [previous],
                        # Сохраняем поле company.
                        "company": {},
                        # Сохраняем состояние поиска.
                        "status": "stalled",
                    },
                )
                # Выполняем повторное чтение.
                result = collect(page, OID, URL, target, None, 5)
                # Оценка берётся из текущего DOM в обоих случаях.
                self.assertEqual(result["reviews"][0]["rating"], 5)
                # Совпавшее короткое начало сохраняет полный текст; новая версия заменяет старую.
                self.assertEqual(
                    # Совпавшее короткое начало сохраняет полный текст; новая версия заменяет старую.
                    result["reviews"][0]["text"],
                    # Совпавшее короткое начало сохраняет полный текст; новая версия заменяет старую.
                    previous["text"] if truncated else text,
                )

    # Изменённое короткое начало не должно ошибочно сохранять прежний полный текст.
    def test_edited_collapsed_text_is_expanded_again(self):
        # Сохранённый и текущий тексты относятся к одному профилю и дате.
        previous = dict(row(1), text="Прежний полный текст.")
        # Изменённое начало пока показано сокращённо.
        current = dict(row(1), text="Новое начало…", text_truncated=True)
        # Встроенный HTML не содержит старую версию текста.
        page = fake_page(example_html(0, 1), {})

        # Раскрытие текущей карточки даёт отредактированный полный текст.
        def evaluate(expression, arg):
            # Передаём текущую версию DOM.
            if expression == EXTRACT:
                # Возвращаем копию, чтобы объединение не изменяло модель страницы.
                return {"company": {}, "reviews": [current.copy()]}
            # Проверяем реальное решение о повторном раскрытии.
            if isinstance(arg, dict) and "fullKeys" in arg:
                # Прежний полный текст не должен отменять раскрытие изменённого начала.
                self.assertEqual(arg["fullKeys"], [])
                # Симулируем результат обычного нажатия кнопки.
                current.update(text="Новое начало. Новый полный текст.", text_truncated=False)
                # Сообщаем о единственном раскрытом отзыве.
                return 1
            # Другие действия не изменяют страницу.
            return 0

        # Подключаем контролируемое раскрытие.
        page.evaluate = evaluate
        # Изолируем прежний и обновлённый снимки.
        with TemporaryDirectory() as folder:
            # Используем один путь для возобновления обработки.
            target = Path(folder) / "reviews.json"
            # Сохраняем прежнюю полную версию.
            save_result(
                # Сохраняем прежнюю полную версию.
                target,
                {
                    # Сохраняем идентификатор организации Яндекса.
                    "organization_id": OID,
                    # Сохраняем поле company.
                    "company": {},
                    # Сохраняем состояние поиска.
                    "status": "stalled",
                    # Сохраняем список отзывов.
                    "reviews": [previous],
                },
            )
            # Выполняем повторное чтение и раскрытие.
            result = collect(page, OID, URL, target, None, 5)
            # Новый полный текст заменяет старый без создания второго отзыва.
            self.assertEqual(
                # Новый полный текст заменяет старый без создания второго отзыва.
                (result["status"], result["expanded_texts"], result["reviews"][0]["text"]),
                # Новый полный текст заменяет старый без создания второго отзыва.
                ("complete", 1, current["text"]),
            )

    # Поиск должен выдержать три настоящие подгрузки после первого просмотра DOM.
    def test_search_initial_dom_is_not_failed_attempt(self):
        # Встроенный HTML и DOM содержат уже известную организацию.
        page = fake_search_page(fixture_html(), [])
        # Изолируем поисковый снимок.
        with TemporaryDirectory() as folder:
            # Лимит больше числа принятых организаций.
            result = discover(page, BBOX, CAFE, Path(folder) / "search.json", 20, 5)
            # Первый просмотр не сокращает число попыток до двух.
            self.assertEqual(
                # Первый просмотр не сокращает число попыток до двух.
                (result["status"], result["scrolls"]),
                # Первый просмотр не сокращает число попыток до двух.
                ("visible_results_exhausted", 3),
            )

    # Пустая первая порция поиска должна допускать повтор появления индикатора.
    def test_browser_search_retries_visibility_after_empty_batch(self):
        # Используем локальный Chromium без обращений к источнику.
        with sync_playwright() as playwright:
            # Выбираем установленный браузер.
            browser = playwright.chromium.launch(
                # Выбираем установленный браузер.
                executable_path=os.environ.get("REVIEW_CHROMIUM_PATH")
            )
            # Закрываем браузер при любом исходе проверки.
            try:
                # Создаём отдельную локальную страницу.
                page = browser.new_page()
                # Три организации имеют точные координаты внутри области и рубрику кафе.
                cards = [
                    # Три организации имеют точные координаты внутри области и рубрику кафе.
                    f'<div class="search-business-snippet-view" data-coordinates="37.6,55.7" style="min-height:180px"><a href="https://yandex.ru/maps/org/example/{index}/"><span class="search-business-snippet-view__title">Кафе {index}</span></a><span class="search-business-snippet-view__category">Кафе</span></div>'
                    # Три организации имеют точные координаты внутри области и рубрику кафе.
                    for index in [101, 102, 103]
                ]
                # В начале доступны только первые две организации.
                html = (
                    # В начале доступны только первые две организации.
                    '<div id="panel" style="height:200px;overflow-y:auto">'
                    # В начале доступны только первые две организации.
                    + "".join(cards[:2])
                    # В начале доступны только первые две организации.
                    + '<div id="loading" style="height:40px"></div></div>'
                )
                # Формируем публичный DOM без встроенного состояния.
                page.set_content(html)
                # Третья организация появляется только после повторной видимости индикатора.
                page.evaluate(
                    """card => {
                  // Считаем появления индикатора в прокручиваемой панели.
                  window.loads=0;
                  // Наблюдаем только обычное пересечение индикатора с окном панели.
                  const observer=new IntersectionObserver(entries=>{
                    // Уход индикатора из окна не загружает новую порцию.
                    if (!entries.some(entry=>entry.isIntersecting)) return;
                    // Первая попытка имитирует завершённую пустую порцию.
                    window.loads++;
                    // Следующее появление даёт отсутствующую организацию.
                    if (window.loads===2) document.querySelector('#loading').insertAdjacentHTML('beforebegin',card);
                  // Задаём ту же прокручиваемую панель, что использует поиск.
                  },{root:document.querySelector('#panel')});
                  // Подключаем наблюдение без сетевого запроса.
                  observer.observe(document.querySelector('#loading'));
                }""",
                    # Третья организация появляется только после повторной видимости индикатора.
                    cards[2],
                )
                # Сохраняем обычное управление поисковой задачей.
                proxy = fake_search_page(html, [])
                # Чтение и прокрутка выполняются в настоящем DOM.
                proxy.evaluate = page.evaluate
                # Сокращаем только локальное тестовое ожидание, без изменения рабочих настроек.
                proxy.wait_for_timeout = lambda milliseconds: page.wait_for_timeout(100)
                # Изолируем файл результата.
                with TemporaryDirectory() as folder:
                    # Выполняем полный путь поиска до трёх организаций.
                    result = discover(proxy, BBOX, CAFE, Path(folder) / "search.json", 3, 5)
                    # Повтор видимости должен получить третью организацию.
                    self.assertEqual(
                        # Повтор видимости должен получить третью организацию.
                        (result["status"], len(result["organizations"])),
                        # Повтор видимости должен получить третью организацию.
                        ("capped", 3),
                    )
                    # Проверяем, что наблюдатель действительно был вызван повторно.
                    self.assertGreaterEqual(page.evaluate("window.loads"), 2)
            # Освобождаем локальный браузер.
            finally:
                # Не оставляем браузерные процессы в фоне.
                browser.close()

    # Ошибки и незавершённая загрузка поиска не должны создавать готовый кэш.
    def test_search_network_states_are_not_complete_cache(self):
        # Проверяем три различных основания остановки.
        for event, status in [
            # Проверяем три различных основания остановки.
            ("request", "load_timeout"),
            # Проверяем три различных основания остановки.
            ("requestfailed", "network_error"),
            # Проверяем три различных основания остановки.
            ("response", "server_error"),
        ]:
            # Изолируем каждый сетевой исход.
            with self.subTest(event=event), TemporaryDirectory() as folder:
                # Первая организация доступна до подгрузки.
                page = fake_search_page(fixture_html(), [])
                # Регистрируем все обработчики событий.
                emit = events(page)
                # Адрес относится к открытой странице карт.
                request = SimpleNamespace(
                    # Адрес относится к открытой странице карт.
                    url=URL,
                    # Адрес относится к открытой странице карт.
                    resource_type="fetch",
                    # Адрес относится к открытой странице карт.
                    failure="net::ERR_CONNECTION_RESET",
                )
                # HTTP-ответ не содержит тела.
                response = SimpleNamespace(url=URL, request=request, status=503, headers={})
                # Сохраняем чтение исходного поискового DOM.
                evaluate = page.evaluate

                # Начинаем выбранное сетевое событие при поисковой прокрутке.
                def scroll(expression, *args):
                    # Действие относится к следующей порции организаций.
                    if expression == SCROLL_SEARCH:
                        # Передаём только метаданные запроса или ответа.
                        emit(event, response if event == "response" else request)
                    # Прежние организации остаются доступны.
                    return evaluate(expression, *args)

                # Подключаем контролируемую подгрузку.
                page.evaluate = scroll
                # Выполняем основной поиск.
                result = discover(page, BBOX, CAFE, Path(folder) / "search.json", 20, 5)
                # Сетевой исход должен отличаться от исчерпанного видимого списка.
                self.assertEqual(result["status"], status)
                # Частичный снимок требует повторного запуска.
                self.assertFalse(search_cache_complete(result, BBOX, CAFE, 20))
                # При незавершённой загрузке повторных прокруток нет.
                if event == "request":
                    # Ожидания ограничены, но первая загрузка не дублируется.
                    self.assertEqual(result["scrolls"], 1)

    # Отказ первой динамической порции нельзя потерять до первой прокрутки.
    def test_initial_dynamic_failure_is_preserved(self):
        # Проверяем поиск и отзывы тем же сценарием раннего отказа.
        for kind in ["search", "reviews"]:
            # Каждому пути выделяем отдельный файл.
            with self.subTest(kind=kind), TemporaryDirectory() as folder:
                # Оставляем меньше записей, чем требуется для готового результата.
                page = (
                    # Оставляем меньше записей, чем требуется для готового результата.
                    fake_search_page(fixture_html(), [])
                    # Оставляем меньше записей, чем требуется для готового результата.
                    if kind == "search"
                    # Оставляем меньше записей, чем требуется для готового результата.
                    else fake_page(example_html(0, 3), {"company": {}, "reviews": [row(1)]})
                )
                # Подключаем полный набор обработчиков браузерных событий.
                emit = events(page)
                # Необходимая динамическая загрузка прерывается ещё при открытии.
                request = SimpleNamespace(
                    # Необходимая динамическая загрузка прерывается ещё при открытии.
                    url=URL,
                    # Необходимая динамическая загрузка прерывается ещё при открытии.
                    resource_type="fetch",
                    # Необходимая динамическая загрузка прерывается ещё при открытии.
                    failure="net::ERR_CONNECTION_RESET",
                )

                # HTML открывается успешно, но первая динамическая загрузка отказала.
                def goto(*args, **kwargs):
                    # Передаём отказ до первого чтения DOM.
                    emit("requestfailed", request)
                    # Сам HTML-документ имеет успешный статус.
                    return SimpleNamespace(status=200)

                # Подключаем ранний отказ.
                page.goto = goto
                # Проверяем соответствующий потребитель страницы.
                if kind == "search":
                    # Видимая неполная выдача не должна превращаться в готовый кэш.
                    result = discover(page, BBOX, CAFE, Path(folder) / "search.json", 20, 5)
                # Второй потребитель загружает тексты отзывов.
                else:
                    # Уже доступный отзыв сохраняется вместе с диагностикой.
                    result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
                # Ранний отказ должен определять итог, если последующего прогресса не было.
                self.assertEqual(result["status"], "network_error")

    # Воспроизводим большие частичные снимки с известным составом записей.
    def test_large_partial_snapshot_counts_and_provenance(self):
        # Все даты допустимы, а публичные профили различают записи одного дня.
        previous_rows = [dict(row(index), date="2026-09-01") for index in range(1, 664)]
        # При текущем открытии источник показывает только первые 600 отзывов.
        visible = [value.copy() for value in previous_rows[:600]]
        # Счётчик карточки больше обоих объёмов.
        page = fake_page(example_html(0, 689), {"company": {}, "reviews": visible})
        # Изолируем полный и ограниченный пути кэша.
        with TemporaryDirectory() as folder:
            # Для полного сбора используем стандартное имя файла.
            target = review_target(folder, OID, None)
            # Прежний частичный файл содержит 663 записи.
            save_result(
                # Прежний частичный файл содержит 663 записи.
                target,
                {
                    # Сохраняем идентификатор организации Яндекса.
                    "organization_id": OID,
                    # Сохраняем состояние поиска.
                    "status": "stalled",
                    # Сохраняем поле company.
                    "company": {"reviews_count": 689},
                    # Сохраняем список отзывов.
                    "reviews": previous_rows,
                },
            )
            # Выполняем повтор с прежними ожиданиями и без реальных пауз в тесте.
            result = collect(page, OID, URL, target, None, 5)
            # Сохранённый объём не должен подменять объём подтверждённого DOM.
            self.assertEqual(
                (
                    # Сохранённый объём не должен подменять объём подтверждённого DOM.
                    result["status"],
                    # Сохранённый объём не должен подменять объём подтверждённого DOM.
                    len(result["reviews"]),
                    # Сохранённый объём не должен подменять объём подтверждённого DOM.
                    result["observed_in_dom"],
                    # Сохранённый объём не должен подменять объём подтверждённого DOM.
                    result["unconfirmed_saved_reviews"],
                ),
                # Сохранённый объём не должен подменять объём подтверждённого DOM.
                ("stalled", 663, 600, 63),
            )
            # Проверяем числовое объяснение состава файла.
            completeness = result["completeness"]
            # Разница счётчика и DOM не равна разнице счётчика и всего файла.
            self.assertEqual(
                # Разница счётчика и DOM не равна разнице счётчика и всего файла.
                (completeness["count_minus_dom"], completeness["count_minus_saved"]),
                # Разница счётчика и DOM не равна разнице счётчика и всего файла.
                (89, 26),
            )
            # Источники всех сохранённых записей образуют полный непересекающийся состав.
            self.assertEqual(
                (
                    # Источники всех сохранённых записей образуют полный непересекающийся состав.
                    completeness["saved_from_dom"],
                    # Источники всех сохранённых записей образуют полный непересекающийся состав.
                    completeness["saved_from_html_only"],
                    # Источники всех сохранённых записей образуют полный непересекающийся состав.
                    completeness["retained_from_previous"],
                ),
                # Источники всех сохранённых записей образуют полный непересекающийся состав.
                (600, 0, 63),
            )
            # Только текущие полные тексты подтверждаются заново.
            self.assertEqual(sum(value["full_text_confirmed"] for value in result["reviews"]), 600)
            # Прежние записи не удаляются и остаются явно неподтверждёнными.
            self.assertEqual(
                # Прежние записи не удаляются и остаются явно неподтверждёнными.
                sum(value["observation"] == "previous_snapshot" for value in result["reviews"]),
                # Прежние записи не удаляются и остаются явно неподтверждёнными.
                63,
            )
            # Все исходные смысловые записи сохранены без добавления фиктивных отзывов.
            self.assertEqual(
                # Все исходные смысловые записи сохранены без добавления фиктивных отзывов.
                {value["id"] for value in result["reviews"]},
                # Все исходные смысловые записи сохранены без добавления фиктивных отзывов.
                {review_key(OID, value) for value in previous_rows},
            )
            # Неполный результат не подходит ни полному, ни ограниченному готовому кэшу.
            self.assertFalse(cache_complete(result, OID, None))
            # Ограниченная команда тоже должна посетить источник заново.
            self.assertFalse(cache_complete(result, OID, 50))
            # Из частичного полного файла нельзя создать готовые свежие 50 без посещения сайта.
            self.assertEqual(load_review_cache(review_target(folder, OID, 50), OID, 50), {})
            # Даже ошибочно изменённый общий статус не отменяет неподтверждённые записи.
            self.assertFalse(
                # Даже ошибочно изменённый общий статус не отменяет неподтверждённые записи.
                cache_complete(
                    # Даже ошибочно изменённый общий статус не отменяет неподтверждённые записи.
                    dict(result, status="complete", company={"reviews_count": 663}),
                    # Даже ошибочно изменённый общий статус не отменяет неподтверждённые записи.
                    OID,
                    # Даже ошибочно изменённый общий статус не отменяет неподтверждённые записи.
                    None,
                )
            )

    # Число физических карточек может превышать число разных отзывов.
    def test_duplicate_dom_cards_do_not_prove_completeness(self):
        # Источник повторяет одну карточку вместо отсутствующего двухсотого отзыва.
        visible = [dict(row(index), date="2026-09-01") for index in range(1, 200)]
        # Получаем 200 DOM-карточек, но только 199 смысловых записей.
        visible.append(visible[-1].copy())
        # Счётчик требует 200 разных отзывов.
        page = fake_page(example_html(0, 200), {"company": {}, "reviews": visible})
        # Сохраняем результат в отдельной папке.
        with TemporaryDirectory() as folder:
            # Выполняем обычную проверку полноты.
            result = collect(page, OID, URL, Path(folder) / "reviews.json", None, 5)
            # Повтор карточки не достигает целевого числа уникальных отзывов.
            self.assertEqual((result["status"], len(result["reviews"])), ("stalled", 199))
            # Диагностика явно показывает один повтор последней DOM-порции.
            self.assertEqual(
                # Диагностика явно показывает один повтор последней DOM-порции.
                (result["dom_cards_in_last_batch"], result["duplicate_dom_cards_in_last_batch"]),
                # Диагностика явно показывает один повтор последней DOM-порции.
                (200, 1),
            )
            # Расхождение счётчика и уникальных записей сохраняется.
            self.assertEqual(result["completeness"]["count_minus_dom"], 1)

    # Противоречивый счётчик и неполные поля не разрешают удалить старый прогресс.
    def test_inconsistent_goal_keeps_unconfirmed_previous_rows(self):
        # Первый вариант имеет слишком маленький счётчик; второй не подтверждает полный текст.
        for count, short, expected in [
            # Первый вариант имеет слишком маленький счётчик; второй не подтверждает полный текст.
            (1, False, "count_mismatch"),
            # Первый вариант имеет слишком маленький счётчик; второй не подтверждает полный текст.
            (2, True, "incomplete_fields"),
        ]:
            # Изолируем обе причины незавершённого сбора.
            with self.subTest(count=count), TemporaryDirectory() as folder:
                # Прежний снимок содержит третью запись вне текущего DOM.
                old = [row(1), row(2), row(3)]
                # Текущий DOM показывает только первые две записи.
                visible = [
                    # Текущий DOM показывает только первые две записи.
                    row(1),
                    # Текущий DOM показывает только первые две записи.
                    dict(row(2), text="Текст…" if short else "Текст 2", text_truncated=short),
                ]
                # Встроенный HTML не подтверждает прежние полные тексты.
                page = fake_page(example_html(0, count), {"company": {}, "reviews": visible})
                # Возобновляем обработку по тому же пути.
                target = Path(folder) / "reviews.json"
                # Сохраняем три ранее полученные записи.
                save_result(
                    # Сохраняем три ранее полученные записи.
                    target,
                    # Сохраняем три ранее полученные записи.
                    {"organization_id": OID, "company": {}, "status": "stalled", "reviews": old},
                )
                # Проверяем текущие записи и счётчик.
                result = collect(page, OID, URL, target, None, 5)
                # Незавершённая проверка не должна удалять третью старую запись.
                self.assertEqual((result["status"], len(result["reviews"])), (expected, 3))
                # Эта запись явно отделена от подтверждённых текущим DOM.
                self.assertEqual(result["completeness"]["retained_from_previous"], 1)
                # Противоречивый или неполный состав не является готовым кэшем.
                self.assertFalse(cache_complete(result, OID, None))

    # Совпадение сокращённого начала не подтверждает неизменность остального текста.
    def test_cached_full_text_requires_current_confirmation(self):
        # Сохраняем прежний полный текст одного отзыва.
        previous = dict(row(1), text="Начало. Прежнее продолжение.")
        # Текущий DOM содержит только одинаковое короткое начало.
        current = dict(row(1), text="Начало…", text_truncated=True)
        # На карточке нет доступной кнопки раскрытия в этом контролируемом сценарии.
        page = fake_page(example_html(0, 1), {"company": {}, "reviews": [current]})
        # Возобновляем из прежнего файла.
        with TemporaryDirectory() as folder:
            # Используем один путь обеих попыток.
            target = Path(folder) / "reviews.json"
            # Сохраняем полный текст, который нельзя удалить при отсутствии нового.
            save_result(
                # Сохраняем полный текст, который нельзя удалить при отсутствии нового.
                target,
                {
                    # Сохраняем идентификатор организации Яндекса.
                    "organization_id": OID,
                    # Сохраняем состояние поиска.
                    "status": "stalled",
                    # Сохраняем поле company.
                    "company": {},
                    # Сохраняем список отзывов.
                    "reviews": [previous],
                },
            )
            # Выполняем повторную проверку.
            result = collect(page, OID, URL, target, None, 5)
            # Наличие сохранённого полного текста не означает, что он подтверждён заново.
            self.assertEqual(
                # Наличие сохранённого полного текста не означает, что он подтверждён заново.
                (result["status"], result["confirmed_full_texts"]),
                # Наличие сохранённого полного текста не означает, что он подтверждён заново.
                ("incomplete_fields", 0),
            )
            # Присутствие записи подтверждено, полный текст остался прежним.
            self.assertEqual(
                # Присутствие записи подтверждено, полный текст остался прежним.
                (result["reviews"][0]["observation"], result["reviews"][0]["full_text_confirmed"]),
                # Присутствие записи подтверждено, полный текст остался прежним.
                ("dom", False),
            )
            # Текст сохраняется для следующей попытки.
            self.assertEqual(result["reviews"][0]["text"], previous["text"])
            # Такой файл не является готовым результатом.
            self.assertFalse(result["completeness"]["ready_as_cache"])

    # Изменение окончания при совпадающем начале требует обычного повторного раскрытия.
    def test_same_prefix_changed_continuation_is_read_again(self):
        # Сохраняем прежний полный текст.
        previous = dict(row(1), text="Начало. Прежнее продолжение.")
        # Новая сокращённая версия ещё выглядит так же.
        current = dict(row(1), text="Начало…", text_truncated=True)
        # HTML не предоставляет текущую полную версию.
        page = fake_page(example_html(0, 1), {})

        # Раскрытие показывает изменённое окончание.
        def evaluate(expression, arg):
            # Возвращаем текущее состояние DOM без изменения модели страницы.
            if expression == EXTRACT:
                # Признак сокращения соответствует реальному отображению.
                return {"company": {}, "reviews": [current.copy()]}
            # Только действительное раскрытие подтверждает полный текст.
            if isinstance(arg, dict) and "fullKeys" in arg:
                # Прежний полный текст не должен исключать эту карточку из раскрытия.
                self.assertEqual(arg["fullKeys"], [])
                # После нажатия появляется новое продолжение.
                current.update(text="Начало. Изменённое продолжение.", text_truncated=False)
                # Подтверждаем единственное раскрытие.
                return 1
            # Остальные действия не меняют модель страницы.
            return 0

        # Подключаем контролируемое чтение.
        page.evaluate = evaluate
        # Изолируем обе версии файла.
        with TemporaryDirectory() as folder:
            # Используем путь возобновления.
            target = Path(folder) / "reviews.json"
            # Сохраняем прежнюю версию текста.
            save_result(
                # Сохраняем прежнюю версию текста.
                target,
                {
                    # Сохраняем идентификатор организации Яндекса.
                    "organization_id": OID,
                    # Сохраняем поле company.
                    "company": {},
                    # Сохраняем состояние поиска.
                    "status": "stalled",
                    # Сохраняем список отзывов.
                    "reviews": [previous],
                },
            )
            # Проводим повторное раскрытие обычным путём.
            result = collect(page, OID, URL, target, None, 5)
            # Изменённое окончание сохранено и подтверждено в этой попытке.
            self.assertEqual(
                # Изменённое окончание сохранено и подтверждено в этой попытке.
                (result["status"], result["reviews"][0]["text"], result["confirmed_full_texts"]),
                # Изменённое окончание сохранено и подтверждено в этой попытке.
                ("complete", current["text"], 1),
            )

    # Ограниченная проекция полного кэша должна пересчитать состав итогового файла.
    def test_complete_cache_projection_recalculates_provenance(self):
        # Три текущих полных отзыва позволяют получить готовый полный снимок.
        page = fake_page(example_html(0, 3), {"company": {}, "reviews": [row(1), row(2), row(3)]})
        # Полный и ограниченный файлы имеют отдельные имена.
        with TemporaryDirectory() as folder:
            # Собираем полный исходный снимок.
            full_target = review_target(folder, OID, None)
            # Все записи подтверждены текущим DOM.
            full = collect(page, OID, URL, full_target, None, 5)
            # Сохраняем точные байты полного файла для проверки неизменности.
            original = full_target.read_bytes()
            # Ограниченную выборку создаём без открытия браузера.
            limited = load_review_cache(review_target(folder, OID, 2), OID, 2)
            # Состав файла должен соответствовать двум оставленным записям.
            self.assertEqual(
                (
                    # Состав файла должен соответствовать двум оставленным записям.
                    limited["completeness"]["saved_reviews"],
                    # Состав файла должен соответствовать двум оставленным записям.
                    limited["completeness"]["saved_from_dom"],
                    # Состав файла должен соответствовать двум оставленным записям.
                    limited["confirmed_full_texts"],
                ),
                # Состав файла должен соответствовать двум оставленным записям.
                (2, 2, 2),
            )
            # Последний реальный исходный запуск наблюдал все три записи.
            self.assertEqual(limited["observed_in_dom"], full["observed_in_dom"])
            # Разница счётчика и ограниченного файла вызвана заданным лимитом.
            self.assertEqual(limited["completeness"]["count_minus_saved"], 1)
            # Полный исходный файл остаётся побайтно прежним.
            self.assertEqual(full_target.read_bytes(), original)
            # Новая ограниченная проекция подходит только готовому ограниченному кэшу.
            self.assertTrue(cache_complete(limited, OID, 2))
            # Ограниченный объём не подтверждает полный результат.
            self.assertFalse(cache_complete(limited, OID, None))

    # Лимит размера границы относится ко всей коллекции, а не каждому объекту.
    def test_feature_collection_aggregate_vertex_limit(self):
        # Два простых полигона содержат суммарно десять координат.
        value = {
            # Сохраняем тип объекта для отделения организаций от адресов.
            "type": "FeatureCollection",
            # Сохраняем поле features.
            "features": [
                # Два простых полигона содержат суммарно десять координат.
                {"type": "Feature", "geometry": polygon(0, 0, 1, 1)},
                # Два простых полигона содержат суммарно десять координат.
                {"type": "Feature", "geometry": polygon(2, 2, 3, 3)},
            ],
        }
        # Малый тестовый предел воспроизводит обход без большого файла.
        with patch("area_geometry.MAX_BOUNDARY_VERTICES", 9):
            # Превышение должно обнаруживаться до объединения геометрий.
            with self.assertRaises(ValueError):
                # Проверяем обычный путь загрузки коллекции.
                load_boundary(value)

    # Журнал различает ещё не начатые и незавершённые карточки.
    def test_queue_records_retry_ids(self):
        # Используем общий образец отказа коллектора.
        from test_resilience import outcome

        # Первая ошибка останавливает очередь перед второй организацией.
        with TemporaryDirectory() as folder, patch(
            # Первая ошибка останавливает очередь перед второй организацией.
            "collect_yandex.collect",
            # Первая ошибка останавливает очередь перед второй организацией.
            return_value=outcome(OID, "network_error")
            # Первая ошибка останавливает очередь перед второй организацией.
        ), patch("sys.stdout"):
            # Создаём два отдельных задания.
            jobs = [(OID, URL, Path(folder) / "one.json"), ("987", URL, Path(folder) / "two.json")]
            # Сохраняем журнал очереди.
            target = Path(folder) / "jobs.json"
            # Выполняем последовательную очередь без реальных обращений к сайту.
            collect_many(fake_page("", {}), jobs, None, 5, 20, 0, "test", target)
            # Читаем журнал так же, как внешний потребитель результатов.
            result = json.loads(target.read_text(encoding="utf-8"))
            # Вторая организация ещё не открывалась.
            self.assertEqual(result["remaining_ids"], ["987"])
            # Первая требует повторной обработки, хотя попытка уже состоялась.
            self.assertEqual(result["retry_ids"], [OID])

    # Проверяем уведомление о подгрузке и лимит коллекции в настоящем браузере.
    def test_browser_middle_insert_reordering_and_geojson_budget(self):
        # Браузер работает только с локально заданным HTML.
        with sync_playwright() as playwright:
            # Используем установленный Chromium без сетевых запросов к Яндексу.
            browser = playwright.chromium.launch(
                # Используем установленный Chromium без сетевых запросов к Яндексу.
                executable_path=os.environ.get("REVIEW_CHROMIUM_PATH")
            )
            # Освобождаем браузер при любом результате проверки.
            try:
                # Создаём отдельную страницу локального сценария.
                page = browser.new_page()
                # Два исходных отзыва задают известные признаки.
                page.set_content(example_html(2, 3))
                # Запоминаем признаки всех видимых карточек.
                known = page.evaluate(DOM_KEYS, ".business-reviews-card-view__review")
                # Передаём те же признаки условию ожидания.
                arg = {"selector": ".business-reviews-card-view__review", "known": known}
                # Переставляем две известные карточки, не добавляя данных.
                page.evaluate(
                    # Переставляем две известные карточки, не добавляя данных.
                    "() => {document.body.appendChild(document.querySelector('.business-reviews-card-view__review'));}"
                )
                # Перестановка не должна пробуждать ожидание новых записей.
                self.assertFalse(page.evaluate(DOM_PROGRESS, arg))
                # Добавляем новую карточку в середину, оставляя последнюю прежней.
                page.evaluate(
                    """() => {
                  // Находим последнюю известную карточку.
                  const last=[...document.querySelectorAll('.business-reviews-card-view__review')].at(-1);
                  // Копируем структуру обычного отзыва.
                  const next=last.cloneNode(true);
                  // Новый профиль отличает новую запись без изменения хвоста списка.
                  next.querySelector('.business-review-view__link').setAttribute('href','https://yandex.ru/maps/user/new');
                  // Вставляем запись перед прежней последней карточкой.
                  last.before(next);
                }"""
                )
                # Новая запись в середине должна завершать ожидание.
                self.assertTrue(page.evaluate(DOM_PROGRESS, arg))
                # Читаем именно функцию проверки из публичного интерфейса.
                source = (Path(__file__).parent / "area_selection.js").read_text(encoding="utf-8")
                # Изолируем проверку от отображения карты и внешних библиотек.
                check = source[
                    # Изолируем проверку от отображения карты и внешних библиотек.
                    source.index("function checkGeoJSON") : source.index("function selectGeometry")
                ]
                # Создаём две части, каждая меньше предела, но вместе больше него.
                message = page.evaluate(
                    # Создаём две части, каждая меньше предела, но вместе больше него.
                    "() => {"
                    # Создаём две части, каждая меньше предела, но вместе больше него.
                    + check
                    # Создаём две части, каждая меньше предела, но вместе больше него.
                    + "const ring=Array.from({length:100001},()=>[0,0]); const value={type:'FeatureCollection',features:[1,2].map(()=>({type:'Feature',geometry:{type:'Polygon',coordinates:[ring]}}))}; try {checkGeoJSON(value); return null;} catch(error) {return error.message;}}"
                )
                # Интерфейс должен отклонить превышение общего предела вершин.
                self.assertIn("во всей границе", message)
            # Закрываем браузер после проверки.
            finally:
                # Ни один процесс Chromium не остаётся в фоне.
                browser.close()


# Разрешаем отдельный запуск этого набора проверок.
if __name__ == "__main__":
    # Возвращаем стандартный код завершения тестов.
    unittest.main()
