"""Автономные проверки отказов, восстановления кэша и записи очереди."""

# Перехватываем диагностические сообщения.
import io

# Проверяем освобождение замка после аварийного завершения своего процесса.
import subprocess

# Запускаем дочернюю проверку тем же интерпретатором.
import sys

# Запускаем тесты стандартной библиотекой.
import unittest

# Изолируем результаты от реальных отзывов.
from tempfile import TemporaryDirectory

# Формируем локальные пути файлов.
from pathlib import Path

# Подменяем только внешние действия и временные ожидания.
from unittest.mock import patch, MagicMock

# Имитируем таймаут и закрытие браузера без обращения к сайту.
from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout

# Проверяем сохранение и чтение снимков.
from storage import save_result, read_snapshot, output_lock

# Проверяем очередь, кэш и стандартные коды ошибок запуска.
from collect_yandex import cache_complete, collect, collect_many, retry_seconds, run_cli

# Проверяем поиск и строгий разбор настроек.
from discover_yandex import discover, resolve_searches, search_snapshot_valid

# Проверяем полный CLI поиска на управляемом браузерном контексте.
from discover_yandex import main as discovery_main

# Повторно используем вымышленные поисковые ответы.
from test_discovery import BBOX, CAFE, fake_search_page, fixture_html

# Повторно используем вымышленную карточку организации.
from test_reviews import OID, URL, example_html, fake_page


# Создаём минимальный результат для проверки очереди без сетевой нагрузки.
def outcome(oid, status="capped", retry_after=None):
    # Поля соответствуют контракту общего обработчика карточек.
    return {
        # Сохраняем версию формата снимка.
        "schema_version": 1,
        # Сохраняем идентификатор организации Яндекса.
        "organization_id": oid,
        # Сохраняем состояние поиска.
        "status": status,
        # Сохраняем время начала попытки.
        "collected_at": "2026-10-02T00:00:00Z",
        # Сохраняем число отзывов с полными полями.
        "valid_reviews": 0,
        # Сохраняем список отзывов.
        "reviews": [],
        # Сохраняем поле company.
        "company": {},
        # Сохраняем показатели браузерных запросов.
        "network": {"issues": []},
        # Сохраняем серверную длительность ожидания.
        "retry_after": retry_after,
        # Сохраняем число свёрнутых текстов.
        "truncated_count": 0,
        # Сохраняем время попытки.
        "elapsed_seconds": 0,
        # Сохраняем число прокруток.
        "scrolls": 0,
        # Сохраняем число раскрытых текстов.
        "expanded_texts": 0,
    }


# Объединяем реальные файловые операции и управляемые отказы браузера.
class ResilienceTests(unittest.TestCase):
    # Повреждённый кэш не должен удаляться или вызывать необработанное исключение.
    def test_corrupt_cache_backup_and_types(self):
        # Изолируем резервные копии проверочных файлов.
        with TemporaryDirectory() as folder:
            # Проверяем неверный JSON, корень-массив и неверную структуру кандидатов.
            for content in [b"{broken", b"[]", b'{"candidates":[{"organization_id":[]}]}']:
                # Создаём контролируемо повреждённый снимок.
                target = Path(folder) / "search.json"
                # Сохраняем точные байты для проверки резервной копии.
                target.write_bytes(content)
                # Не печатаем диагностические сообщения автономного теста.
                with patch("sys.stdout", new_callable=io.StringIO):
                    # Неверная структура приводит к новому снимку.
                    self.assertEqual(read_snapshot(target, search_snapshot_valid), {})
                # Исходное содержимое должно сохраниться без изменений.
                self.assertIn(
                    # Исходное содержимое должно сохраниться без изменений.
                    content,
                    # Исходное содержимое должно сохраниться без изменений.
                    [p.read_bytes() for p in Path(folder).glob("*.broken-*.json")],
                )
            # Неверный корень и нестроковый ключ отзыва дают False вместо исключения.
            for value in [[], None, {"company": []}, {"reviews": [{"id": []}]}]:
                # Проверяем публичный предикат готовности кэша.
                self.assertFalse(cache_complete(value, OID, 20))

    # Ошибка записи не должна менять уже готовый JSON.
    def test_atomic_write_failures_preserve_previous(self):
        # Создаём отдельную папку снимка.
        with TemporaryDirectory() as folder:
            # Формируем итоговый путь.
            target = Path(folder) / "result.json"
            # Записываем корректную предыдущую версию.
            save_result(target, {"version": 1})
            # Фиксируем точные байты контрольного файла.
            previous = target.read_bytes()
            # Проверяем ошибку сериализации.
            with self.assertRaises(TypeError):
                # Множество не является обычным JSON.
                save_result(target, {"bad": {1, 2}})
            # Нехватка места в ходе записи также не должна менять прежний файл.
            with patch("storage.json.dump", side_effect=OSError(28, "disk full")):
                # Ошибка диска явно передаётся обработчику запуска.
                with self.assertRaises(OSError):
                    # Запись новой версии не завершается успешно.
                    save_result(target, {"version": 2})
            # Проверяем отказ замены файла со стороны файловой системы.
            with patch("pathlib.Path.replace", side_effect=PermissionError("locked")):
                # Отказ не считается успешно записанным результатом.
                with self.assertRaises(PermissionError):
                    # Новая версия не должна повредить прежний файл.
                    save_result(target, {"version": 2})
            # Старый снимок остаётся побитно неизменным.
            self.assertEqual(target.read_bytes(), previous)
            # Собственные временные файлы убраны после отказов.
            self.assertEqual(list(Path(folder).glob("*.tmp")), [])

    # Две очереди не должны одновременно обращаться к сайту из одной папки.
    def test_folder_lock_released_after_process_exit(self):
        # Используем отдельную папку результата.
        with TemporaryDirectory() as folder:
            # Первая очередь получает исключительный замок.
            with output_lock(folder):
                # Вторая очередь должна получить понятную ошибку сразу.
                with self.assertRaisesRegex(RuntimeError, "занята"):
                    # Не ожидаем завершения первой очереди бесконечно.
                    with output_lock(folder):
                        # Эта строка не должна выполниться.
                        self.fail("Вторая очередь получила замок")
            # Проверяем освобождение после жёсткого завершения собственного дочернего процесса.
            code = "from storage import output_lock; import os,sys; lock=output_lock(sys.argv[1]); lock.__enter__(); os._exit(7)"
            # Дочерняя проверка не открывает браузер и не делает сетевых запросов.
            child = subprocess.run(
                # Дочерняя проверка не открывает браузер и не делает сетевых запросов.
                [sys.executable, "-c", code, folder],
                # Дочерняя проверка не открывает браузер и не делает сетевых запросов.
                cwd=Path(__file__).parent,
                # Дочерняя проверка не открывает браузер и не делает сетевых запросов.
                timeout=10,
            )
            # Завершение произошло внутри удерживаемого замка.
            self.assertEqual(child.returncode, 7)
            # Новый процесс может возобновить работу без удаления файла замка.
            with output_lock(folder):
                # Системная блокировка освобождена после аварийного выхода.
                self.assertTrue((Path(folder) / ".run.lock").exists())

    # Неверные настройки не должны превращаться в неожиданные запросы.
    def test_input_lists_and_retry_values(self):
        # Проверяем строку вместо списка, null и нестроковые элементы.
        for categories, queries in [("cafe", []), ([], "кафе"), ([None], []), (None, [])]:
            # Каждый вариант даёт ошибку до запуска сети.
            with self.assertRaises(ValueError):
                # Не разбираем поисковое выражение по буквам.
                resolve_searches(categories, queries)
        # Неверный заголовок не создаёт бесконечную задержку.
        for value in ["inf", "Infinity", "nan", "не число", "-12"]:
            # Неверные или отрицательные интервалы игнорируются.
            self.assertEqual(retry_seconds(value), 0)
        # Корректное значение сервера сохраняется.
        self.assertEqual(retry_seconds("120"), 120)

    # Повтор после 429 не должен изменять паузу между карточками.
    def test_rate_limit_recovery_and_intercard_pause(self):
        # Изолируем снимки и журнал очереди.
        with TemporaryDirectory() as folder:
            # Создаём две карточки для последовательного обхода.
            jobs = [(oid, URL, Path(folder) / f"{oid}.json") for oid in ["1", "2"]]
            # Первая карточка проходит после одного 429, вторая сразу готова.
            values = [outcome("1", "rate_limited", "60"), outcome("1"), outcome("2")]
            # Ожидание подменяется только в автономной проверке.
            with patch("collect_yandex.collect", side_effect=values), patch(
                # Ожидание подменяется только в автономной проверке.
                "collect_yandex.time.sleep"
                # Ожидание подменяется только в автономной проверке.
            ) as sleep, patch("sys.stdout", new_callable=io.StringIO):
                # Проверяем весь общий обработчик очереди.
                result = collect_many(
                    # Проверяем весь общий обработчик очереди.
                    None,
                    # Проверяем весь общий обработчик очереди.
                    jobs,
                    # Передаём межкарточную паузу двадцать секунд.
                    20,
                    # Передаём допустимую паузу или небольшой объём автономной проверки.
                    5,
                    # Передаём межкарточную паузу двадцать секунд.
                    20,
                    # Проверяем весь общий обработчик очереди.
                    1,
                    # Задаём условный User-Agent без обращения к внешнему браузеру.
                    "Chrome",
                    # Проверяем весь общий обработчик очереди.
                    Path(folder) / "jobs.json",
                )
            # После 429 ждём 60 секунд, между карточками — прежние 20.
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 20])
            # Обе карточки действительно обработаны.
            self.assertEqual(len(result), 2)
            # Очередь завершена и незапрошенных идентификаторов нет.
            self.assertEqual(read_snapshot(Path(folder) / "jobs.json")["remaining_ids"], [])

    # Ограничение доступа прекращает очередь, сохраняя незапрошенные карточки.
    def test_access_stops_and_long_retry_after(self):
        # Проверяем каждый терминальный исход независимо.
        for status in ["captcha", "access_denied", "browser_closed", "rate_limited"]:
            # Используем отдельную папку на один сценарий.
            with TemporaryDirectory() as folder:
                # Вторая карточка не должна открываться.
                jobs = [(oid, URL, Path(folder) / f"{oid}.json") for oid in ["1", "2"]]
                # Рекомендация 3600 секунд прекращает повторы текущего запуска.
                with patch(
                    # Рекомендация 3600 секунд прекращает повторы текущего запуска.
                    "collect_yandex.collect",
                    # Рекомендация 3600 секунд прекращает повторы текущего запуска.
                    return_value=outcome("1", status, "3600")
                    # Рекомендация 3600 секунд прекращает повторы текущего запуска.
                ) as fetch, patch("collect_yandex.time.sleep") as sleep, patch(
                    # Рекомендация 3600 секунд прекращает повторы текущего запуска.
                    "sys.stdout",
                    # Рекомендация 3600 секунд прекращает повторы текущего запуска.
                    new_callable=io.StringIO,
                ):
                    # Число разрешённых повторов не отменяет терминальные ограничения.
                    collect_many(None, jobs, 20, 5, 20, 3, "Chrome", Path(folder) / "jobs.json")
                # Не выполняем второй запрос или бесконечное ожидание.
                self.assertEqual(fetch.call_count, 1)
                # Первая попытка не требует межкарточной паузы.
                sleep.assert_not_called()
                # Незавершённая очередь явно отмечена.
                journal = read_snapshot(Path(folder) / "jobs.json")
                # Вторая карточка остаётся для следующего запуска.
                self.assertEqual(journal["remaining_ids"], ["2"])
                # Статус не выдаёт остановку за завершённую очередь.
                self.assertEqual(journal["status"], "stopped")

    # Ctrl+C сохраняет отдельный статус карточки, поиска и общей очереди.
    def test_interruption_checkpoints(self):
        # Изолируем результаты прерывания.
        with TemporaryDirectory() as folder:
            # Создаём управляемую карточку.
            page = fake_page(example_html(), {"company": {}, "reviews": []})
            # Прерываем до завершения открытия страницы.
            page.goto = lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
            # Верхний уровень должен получить прерывание.
            with self.assertRaises(KeyboardInterrupt):
                # Уже начатая карточка сохраняет свой статус.
                collect(page, OID, URL, Path(folder) / "review.json", 20, 0)
            # Снимок не остаётся в неопределённом состоянии running.
            self.assertEqual(read_snapshot(Path(folder) / "review.json")["status"], "interrupted")
            # Проверяем тот же сценарий поиска.
            search = fake_search_page(fixture_html(), [])
            # Имитируем прерывание браузерного открытия.
            search.goto = page.goto
            # Поиск сохраняет свой статус до выхода из очереди.
            with self.assertRaises(KeyboardInterrupt):
                # Новые карточки после прерывания не открываются.
                discover(search, BBOX, CAFE, Path(folder) / "search.json")
            # Контрольная точка поиска сохранена.
            self.assertEqual(read_snapshot(Path(folder) / "search.json")["status"], "interrupted")
            # Общая очередь также должна сохранить своё положение.
            with patch("collect_yandex.collect", side_effect=KeyboardInterrupt()), patch(
                # Общая очередь также должна сохранить своё положение.
                "sys.stdout",
                # Общая очередь также должна сохранить своё положение.
                new_callable=io.StringIO,
            ):
                # Не превращаем прерывание в обычную ошибку карточки.
                with self.assertRaises(KeyboardInterrupt):
                    # Проверяем сохранение журнала.
                    collect_many(
                        # Проверяем сохранение журнала.
                        None,
                        # Проверяем сохранение журнала.
                        [(OID, URL, Path(folder) / "review.json")],
                        # Передаём межкарточную паузу двадцать секунд.
                        20,
                        # Передаём допустимую паузу или небольшой объём автономной проверки.
                        5,
                        # Передаём межкарточную паузу двадцать секунд.
                        20,
                        # Отключаем повтор после 429 в автономной проверке.
                        0,
                        # Задаём условный User-Agent без обращения к внешнему браузеру.
                        "Chrome",
                        # Проверяем сохранение журнала.
                        Path(folder) / "jobs.json",
                    )
            # Очередь остаётся возобновляемой.
            self.assertEqual(read_snapshot(Path(folder) / "jobs.json")["status"], "interrupted")

    # Ошибки основного HTTP-документа различаются по причине.
    def test_http_missing_and_server_retries(self):
        # Проверяем 404 и исчерпание технических повторов отдельно.
        for code, expected, retries in [(404, "not_found", []), (503, "server_error", [30, 60])]:
            # Изолируем файл каждой карточки.
            with TemporaryDirectory() as folder, patch("collect_yandex.time.sleep") as sleep:
                # У ответа нет рабочего DOM, поэтому он не должен читаться.
                page = fake_page("", {}, code)
                # Проверяем обработку обычного браузерного HTTP-ответа.
                result = collect(page, OID, URL, Path(folder) / "review.json", 20, 0)
                # Статус не выдаёт ошибку сайта за отсутствие отзывов.
                self.assertEqual(result["status"], expected)
                # Технический повтор имеет ограниченные паузы.
                self.assertEqual([call.args[0] for call in sleep.call_args_list], retries)

    # Таймаут и закрытый браузер не должны давать бесконечные попытки.
    def test_timeout_and_closed_browser(self):
        # Проверяем каждый вид отказа независимо.
        for closed, error, expected in [
            # Проверяем каждый вид отказа независимо.
            (False, BrowserTimeout("timeout"), "timeout"),
            # Проверяем каждый вид отказа независимо.
            (True, BrowserError("browser closed"), "browser_closed"),
        ]:
            # Изолируем файл одной попытки.
            with TemporaryDirectory() as folder, patch("collect_yandex.time.sleep"):
                # Создаём обычную подмену браузерной страницы.
                page = fake_page("", {})
                # Задаём проверяемое состояние рабочей вкладки.
                page.is_closed = lambda: closed
                # Отказ возникает при открытии карточки.
                page.goto = lambda *args, **kwargs: (_ for _ in ()).throw(error)
                # Проверяем итог после ограниченного количества попыток.
                self.assertEqual(
                    # Проверяем итог после ограниченного количества попыток.
                    collect(page, OID, URL, Path(folder) / "review.json", 20, 0)["status"],
                    # Проверяем итог после ограниченного количества попыток.
                    expected,
                )

    # Нагрузка на очередь и диск проверяется без массовых запросов к сайту.
    def test_local_queue_twenty_jobs(self):
        # Изолируем двадцать результатов.
        with TemporaryDirectory() as folder:
            # Создаём набор карточек с уникальными идентификаторами.
            jobs = [(str(i), URL, Path(folder) / f"{i}.json") for i in range(20)]
            # Подменяем только сетевой сбор, оставляя реальные записи файлов и журнал.
            with patch(
                # Подменяем только сетевой сбор, оставляя реальные записи файлов и журнал.
                "collect_yandex.collect",
                # Подменяем только сетевой сбор, оставляя реальные записи файлов и журнал.
                side_effect=lambda page, oid, *args: outcome(oid)
                # Подменяем только сетевой сбор, оставляя реальные записи файлов и журнал.
            ), patch("collect_yandex.time.sleep"), patch("sys.stdout", new_callable=io.StringIO):
                # Проходим весь набор последовательно.
                result = collect_many(
                    # Проходим весь набор последовательно.
                    None,
                    # Проходим весь набор последовательно.
                    jobs,
                    # Передаём межкарточную паузу двадцать секунд.
                    20,
                    # Передаём допустимую паузу или небольшой объём автономной проверки.
                    5,
                    # Передаём межкарточную паузу двадцать секунд.
                    20,
                    # Отключаем повтор после 429 в автономной проверке.
                    0,
                    # Задаём условный User-Agent без обращения к внешнему браузеру.
                    "Chrome",
                    # Проходим весь набор последовательно.
                    Path(folder) / "jobs.json",
                )
            # Ни одна карточка не потеряна в журнале.
            self.assertEqual(len(result), 20)
            # Все файлы читаются как целые снимки.
            self.assertEqual(
                # Все файлы читаются как целые снимки.
                [read_snapshot(path)["organization_id"] for _, _, path in jobs],
                # Все файлы читаются как целые снимки.
                [oid for oid, _, _ in jobs],
            )
            # Завершение очереди фиксируется после последней записи.
            self.assertEqual(read_snapshot(Path(folder) / "jobs.json")["status"], "finished")

    # Автоматический запуск получает ненулевой код и понятную диагностику отказа.
    def test_cli_error_codes(self):
        # Проверяем ошибку диска и пользовательское прерывание.
        for error, expected in [
            # Проверяем ошибку диска и пользовательское прерывание.
            (PermissionError("Нет доступа к файлу"), 1),
            # Проверяем ошибку диска и пользовательское прерывание.
            (KeyboardInterrupt(), 130),
        ]:
            # Перехватываем сообщения без печати длинной трассировки.
            with patch("sys.stderr", new_callable=io.StringIO) as stream:
                # Неверный запуск не должен завершиться кодом ноль.
                with self.assertRaises(SystemExit) as stopped:
                    # Вызываем обработчик с управляемым отказом.
                    run_cli(lambda: (_ for _ in ()).throw(error))
                # Код соответствует причине завершения.
                self.assertEqual(stopped.exception.code, expected)
                # Пользователь получает сообщение о причине.
                self.assertTrue(stream.getvalue())

    # Флаг отзывов и остановка поиска должны управлять реальным путём CLI.
    def test_discovery_cli_review_gate_and_exit_code(self):
        # Проверяем отсутствие флага, обычный сбор, CAPTCHA и неполную карточку.
        for requested, search_status, review_status in [
            # Проверяем отсутствие флага, обычный сбор, CAPTCHA и неполную карточку.
            (False, "capped", "capped"),
            # Проверяем отсутствие флага, обычный сбор, CAPTCHA и неполную карточку.
            (True, "capped", "capped"),
            # Проверяем отсутствие флага, обычный сбор, CAPTCHA и неполную карточку.
            (True, "captcha", "capped"),
            # Проверяем отсутствие флага, обычный сбор, CAPTCHA и неполную карточку.
            (True, "capped", "incomplete_fields"),
        ]:
            # Изолируем каждую проверку полного пути запуска.
            with TemporaryDirectory() as folder:
                # Подготавливаем управляемый Chromium без сети.
                browser = MagicMock()
                # Временная проверочная вкладка сообщает обычный User-Agent.
                browser.new_page.return_value.evaluate.return_value = "Chrome"
                # Страница рабочего контекста доступна общему обработчику.
                browser.new_context.return_value.new_page.return_value = MagicMock()
                # Управляем запуском браузера из sync_playwright.
                playwright = MagicMock()
                # Возвращаем подготовленный объект браузера.
                playwright.chromium.launch.return_value = browser
                # Создаём организацию внутри вымышленного прямоугольника.
                row = {
                    # Сохраняем идентификатор организации Яндекса.
                    "organization_id": "101",
                    # Сохраняем адрес публичной карточки.
                    "url": "https://yandex.ru/maps/org/101/reviews/",
                    # Сохраняем координаты в порядке долгота, широта.
                    "coordinates_lon_lat": [37.6, 55.7],
                    # Сохраняем фактические названия рубрик.
                    "rubrics": ["Кафе"],
                }
                # Создаём ответ поиска с явным статусом.
                result = {"status": search_status, "organizations": [row]}
                # Формируем штатные параметры CLI.
                argv = [
                    # Формируем штатные параметры CLI.
                    "discover_yandex.py",
                    # Задаём имя параметра командной строки.
                    "--bbox",
                    # Формируем штатные параметры CLI.
                    *map(str, BBOX),
                    # Задаём имя параметра командной строки.
                    "--categories",
                    # Формируем штатные параметры CLI.
                    "cafe",
                    # Задаём имя параметра командной строки.
                    "--output",
                    # Формируем штатные параметры CLI.
                    folder,
                    # Задаём имя параметра командной строки.
                    "--max-orgs",
                    # Формируем штатные параметры CLI.
                    "1",
                    # Задаём имя параметра командной строки.
                    "--429-retries",
                    # Формируем штатные параметры CLI.
                    "0",
                ]
                # Включаем отзывы только в выбранных сценариях.
                if requested:
                    # Флаг передаётся тем же способом, что в обычной команде.
                    argv.append("--with-reviews")
                # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                with patch("sys.argv", argv), patch(
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                    "discover_yandex.sync_playwright"
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                ) as start, patch("discover_yandex.discover", return_value=result), patch(
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                    "discover_yandex.collect_many",
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                    return_value=[{"status": review_status}]
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                ) as reviews, patch(
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                    "discover_yandex.time.sleep"
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                ), patch(
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                    "sys.stdout",
                    # Подменяем сеть, оставляя проверку аргументов и запись файлов.
                    new_callable=io.StringIO,
                ):
                    # Контекстный менеджер возвращает управляемый Playwright.
                    start.return_value.__enter__.return_value = playwright
                    # CAPTCHA или неполные поля требуют ненулевого кода завершения.
                    if search_status == "captcha" or review_status == "incomplete_fields":
                        # Завершение не должно выглядеть успешным для автоматического запуска.
                        with self.assertRaises(SystemExit) as stopped:
                            # Выполняем штатный main без вызова внешнего сайта.
                            discovery_main()
                        # Код один означает незавершённый или ошибочный сбор.
                        self.assertEqual(stopped.exception.code, 1)
                    # Обычный поиск с готовыми результатами завершается штатно.
                    else:
                        # Проверяем как поиск без отзывов, так и сбор по флагу.
                        discovery_main()
                    # После CAPTCHA либо без флага очередь отзывов не запускается.
                    self.assertEqual(
                        # После CAPTCHA либо без флага очередь отзывов не запускается.
                        reviews.call_count,
                        # После CAPTCHA либо без флага очередь отзывов не запускается.
                        int(requested and search_status == "capped"),
                    )
                # Browser закрывается при успешном и ошибочном исходе.
                browser.close.assert_called_once()

    # Повреждённые даты и нестандартный JSON не должны считаться готовым кэшем.
    def test_invalid_dates_and_nonfinite_json(self):
        # Берём результат с допустимой формой и одной полной записью.
        row = {
            # Сохраняем вымышленный идентификатор проверки.
            "id": "one",
            # Сохраняем поисковый запрос.
            "text": "Текст",
            # Сохраняем поле rating.
            "rating": 5,
            # Сохраняем поле text_truncated.
            "text_truncated": False,
            # Сохраняем поле date.
            "date": {"bad": 1},
        }
        # Неверный тип даты исключает готовность результата.
        self.assertFalse(
            # Неверный тип даты исключает готовность результата.
            cache_complete({"organization_id": OID, "status": "capped", "reviews": [row]}, OID, 1)
        )
        # Сохраняем повреждённый файл отдельно от рабочих данных.
        with TemporaryDirectory() as folder:
            # Создаём заведомо нестандартный JSON.
            target = Path(folder) / "sample.json"
            # NaN не допускается форматом JSON.
            target.write_text('{"value": NaN}', encoding="utf-8")
            # Строковый путь поддерживается, повреждение возвращает пустой кэш.
            self.assertEqual(read_snapshot(str(target)), {})
            # Исходные байты остаются в единственной резервной копии.
            self.assertEqual(len(list(Path(folder).glob("*.broken-*.json"))), 1)

    # Поисковая строка и цитата в отзыве не должны изображать CAPTCHA.
    def test_captcha_false_positives_and_coordinates(self):
        # Импортируем функции, используемые именно в этой проверке.
        from collect_yandex import challenge

        # Проверяем координаты до пространственного отбора.
        from discover_yandex import valid_coordinates

        # Подготавливаем страницу поиска с обычным текстом.
        page = MagicMock()
        # Слово в параметре запроса не является адресом проверки пользователя.
        page.url = "https://yandex.ru/maps/?text=captcha"
        # Слова в середине отзыва также не являются заголовком проверки.
        page.locator.return_value.inner_text.return_value = "Кафе. В отзыве: вы не робот"
        # Поиск продолжается без ложной остановки.
        self.assertFalse(challenge(page))
        # Настоящий адрес проверки останавливает сбор независимо от текста.
        page.url = "https://yandex.ru/showcaptcha?retpath=maps"
        # Проверка маршрута не зависит от наличия параметров.
        self.assertTrue(challenge(page))
        # Отклоняем логические координаты вместо преобразования в ноль и единицу.
        self.assertIsNone(valid_coordinates([True, False]))
        # Переполнение числа также означает непроверенное положение.
        self.assertIsNone(valid_coordinates([10**1000, 55]))

    # Противоречивый нулевой счётчик не должен уничтожать реальные записи.
    def test_zero_count_preserves_loaded_reviews(self):
        # Каждая проверка использует собственный снимок.
        with TemporaryDirectory() as folder:
            # HTML содержит отзыв, хотя счётчик сообщает ноль.
            page = fake_page(example_html(1, 0), {})
            # Сбор должен остановиться до чтения следующих порций.
            result = collect(page, OID, URL, Path(folder) / "result.json", 20, 0)
            # Отмечаем противоречие, сохранив исходный текст.
            self.assertEqual(result["status"], "count_mismatch")
            # Счётчик не удалил извлечённую запись.
            self.assertEqual(len(result["reviews"]), 1)

    # При одновременном 429 и 503 нельзя повторять запрос как обычную техническую ошибку.
    def test_access_stop_precedes_server_retry(self):
        # Подключаем простой контейнер полей вымышленного ответа.
        from types import SimpleNamespace

        # Рабочие файлы не затрагивают реальные результаты.
        with TemporaryDirectory() as folder:
            # Подготавливаем страницу без обращения к сети.
            page = fake_page(example_html(0, 0), {})
            # Сохраняем обработчик ответов для воспроизведения двух событий.
            callbacks = {}
            # Перехватываем регистрацию события HTTP-ответа.
            page.on = lambda name, callback: callbacks.update({name: callback})

            # Воспроизводим ограничение фоновой загрузки до завершения открытия документа.
            def goto(*args, **kwargs):
                # Сайт ограничил загрузку данных страницы.
                callbacks["response"](
                    # Сайт ограничил загрузку данных страницы.
                    SimpleNamespace(
                        # Сайт ограничил загрузку данных страницы.
                        status=429,
                        # Сайт ограничил загрузку данных страницы.
                        url=URL,
                        # Сайт ограничил загрузку данных страницы.
                        headers={},
                        # Сайт ограничил загрузку данных страницы.
                        request=SimpleNamespace(resource_type="fetch"),
                    )
                )
                # Документ одновременно завершился технической ошибкой.
                return SimpleNamespace(status=503)

            # Используем контролируемую навигацию.
            page.goto = goto
            # Реальных задержек в автономном тесте нет.
            with patch("collect_yandex.time.sleep") as pause:
                # Сбор должен сохранить именно ограничение доступа.
                result = collect(page, OID, URL, Path(folder) / "result.json", 20, 0)
                # Не маскируем ограничение технической ошибкой.
                self.assertEqual(result["status"], "rate_limited")
                # Повтор с задержкой 30 секунд не был назначен.
                pause.assert_not_called()

    # Отсутствующую оценку нельзя заменять предполагаемым числом.
    def test_missing_rating_preserved(self):
        # Используем полноценный текст с отсутствующей индивидуальной оценкой.
        html = example_html(1, 1).replace('"rating": 1', '"rating": null')
        # DOM также не содержит оценки, но подтверждает тот же отзыв.
        batch = {
            # Сохраняем поле company.
            "company": {"reviews_count": 1},
            # Сохраняем список отзывов.
            "reviews": [
                {
                    # Сохраняем поле author.
                    "author": "Автор 0",
                    # Сохраняем поле author_url.
                    "author_url": "https://yandex.ru/maps/user/user-0",
                    # Сохраняем поле date.
                    "date": "2026-09-01",
                    # Сохраняем поисковый запрос.
                    "text": "Полный текст отзыва 0. Продолжение.",
                    # Сохраняем поле rating.
                    "rating": None,
                    # Сохраняем поле text_truncated.
                    "text_truncated": False,
                }
            ],
        }
        # Изолируем контрольный снимок.
        with TemporaryDirectory() as folder:
            # Выполняем тот же сбор, что используется для реальных карточек.
            result = collect(fake_page(html, batch), OID, URL, Path(folder) / "result.json", 20, 0)
            # Неполные поля явно отмечены в результате.
            self.assertEqual(result["status"], "incomplete_fields")
            # Текст сохранён, а оценка не придумана.
            self.assertIsNone(result["reviews"][0]["rating"])
            # Такой снимок не пропускает следующую проверку полноты.
            self.assertFalse(cache_complete(result, OID, 20))


# Разрешаем запуск проверок обычной командой Python.
if __name__ == "__main__":
    # Выполняем все автономные сценарии отказа.
    unittest.main()
