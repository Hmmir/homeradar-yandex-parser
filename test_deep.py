"""Проверки обрыва сети, обновления снимка и устойчивости ключей отзывов."""

# Читаем путь к браузеру автономных проверок.
import os

# Подменяем параметры запуска.
import sys

# Выполняем проверки стандартной библиотекой.
import unittest

# Изолируем проверочные снимки.
from pathlib import Path

# Создаём временные папки результатов.
from tempfile import TemporaryDirectory

# Формируем минимальные ответы браузера.
from types import SimpleNamespace

# Подменяем только ожидания и внешние действия.
from unittest.mock import patch

# Используем настоящий Chromium и управляемые ошибки.
from playwright.sync_api import (
    # Подключаем управление сессией Chromium.
    sync_playwright,
    # Используем настоящий Chromium и управляемые ошибки.
    Error as BrowserError,
    # Используем настоящий Chromium и управляемые ошибки.
    TimeoutError as BrowserTimeout,
)

# Проверяем общий путь сбора и ключи снимка.
from collect_yandex import (
    # Проверяем общий путь сбора и ключи снимка.
    collect,
    # Подключаем проверку достаточности сохранённых отзывов.
    cache_complete,
    # Проверяем общий путь сбора и ключи снимка.
    review_key,
    # Проверяем общий путь сбора и ключи снимка.
    main,
    # Проверяем общий путь сбора и ключи снимка.
    select_newest,
    # Подключаем общий последовательный обработчик карточек отзывов.
    collect_many,
)

# Записываем контрольный снимок тем же способом, что и программа.
from storage import save_result

# Повторно используем вымышленную карточку и браузерную подмену.
from test_reviews import OID, URL, example_html, fake_page

# Повторно используем полные записи с разными датами.
from test_limits import row


# Проверяем сохранение и обновление без реального сайта.
class DeepTests(unittest.TestCase):
    # Отменяем только известные ресурсы фоновой карты, сохраняя загрузку карточки.
    def test_map_resource_filter_preserves_review_loading(self):
        # Подготавливаем совместимый ограниченный результат.
        page = fake_page(
            # Подготавливаем совместимый ограниченный результат.
            example_html(0, 2),
            # Подготавливаем совместимый ограниченный результат.
            {"company": {"reviews_count": 2}, "reviews": [row(1), row(2)]},
        )
        # Запоминаем обработчик фильтра ресурсов.
        handlers = []
        # Подключаем настоящий фильтр сборщика к вымышленным запросам.
        page.route = lambda pattern, handler: handlers.append(handler)
        # Сохраняем исходный управляемый переход.
        navigate = page.goto
        # Создаём два ненужных запроса карты и необходимую загрузку карточки.
        requests = [
            # Создаём два ненужных запроса карты и необходимую загрузку карточки.
            unittest.mock.Mock(request=SimpleNamespace(url=url, resource_type="fetch"))
            # Создаём два ненужных запроса карты и необходимую загрузку карточки.
            for url in [
                # Создаём два ненужных запроса карты и необходимую загрузку карточки.
                "https://core-renderer-tiles.maps.yandex.ru/vmap3/tiles",
                # Создаём два ненужных запроса карты и необходимую загрузку карточки.
                "https://real-time-poi.maps.yandex.ru/icons",
                # Создаём два ненужных запроса карты и необходимую загрузку карточки.
                URL,
            ]
        ]

        # При открытии страницы браузер пропускает запросы через фильтр.
        def goto(*args, **kwargs):
            # Последовательно проверяем каждый тип запроса.
            for request in requests:
                # Используем фильтр, зарегистрированный самим сборщиком.
                handlers[0](request)
            # Возвращаем стандартный ответ вымышленной страницы.
            return navigate(*args, **kwargs)

        # Ресурсы проверяются в обычном пути открытия карточки.
        page.goto = goto
        # Изолируем снимок этого прохода.
        with TemporaryDirectory() as folder:
            # Необходимые поля отзывов должны сохраниться полностью.
            result = collect(page, OID, URL, Path(folder) / "result.json", None, 0)
            # Оба отзыва подтверждены после отмены фоновых ресурсов.
            self.assertEqual((result["status"], result["valid_reviews"]), ("complete", 2))
            # Оба ненужных запроса отменены.
            self.assertEqual(result["network"]["blocked_map_resources"], 2)
            # Документ карточки не должен блокироваться.
            requests[2].continue_.assert_called_once()
            # Данные карты не отправляются на загрузку.
            requests[0].abort.assert_called_once()
            # Значки соседних объектов также не используются.
            requests[1].abort.assert_called_once()

    # Повторный запуск должен объединить старые ключи одного полного текста.
    def test_legacy_anonymous_duplicates_are_rekeyed(self):
        # Отзыв без профиля использует текст при определении ключа.
        full = dict(row(1), author_url=None, text="Начало. Полный текст.")
        # Старый ключ был вычислен по сокращённой версии.
        short_key = review_key(OID, dict(full, text="Начало.", text_truncated=True))
        # Две версии имеют разные старые ключи, но одинаковые полные поля.
        old = dict(full, id=short_key)
        # Новый снимок использовал ключ полного текста.
        canonical = dict(full, id=review_key(OID, full))
        # Такой кэш нельзя объявить полным из-за формально разных идентификаторов.
        previous = {
            # Сохраняем идентификатор организации Яндекса.
            "organization_id": OID,
            # Сохраняем состояние поиска.
            "status": "complete",
            # Сохраняем поле company.
            "company": {"reviews_count": 2},
            # Сохраняем список отзывов.
            "reviews": [old, canonical],
        }
        # Повторяющиеся полные записи отклоняются до сетевого сбора.
        self.assertFalse(cache_complete(previous, OID, None))
        # Изолируем повторную попытку.
        with TemporaryDirectory() as folder:
            # Сохраняем прежний снимок с двумя ключами.
            target = Path(folder) / f"{OID}.json"
            # Восстановление использует настоящий файловый кэш.
            save_result(target, previous)
            # Страница подтверждает один полный отзыв.
            page = fake_page(
                # Страница подтверждает один полный отзыв.
                example_html(0, 1),
                # Страница подтверждает один полный отзыв.
                {"company": {"reviews_count": 1}, "reviews": [full]},
            )
            # Повторный проход должен оставить одну запись с устойчивым ключом.
            result = collect(page, OID, URL, target, None, 0)
            # Сохраняется один подтверждённый отзыв.
            self.assertEqual((result["status"], len(result["reviews"])), ("complete", 1))
            # Ключ больше не зависит от прежнего сокращения.
            self.assertEqual(result["reviews"][0]["id"], review_key(OID, full))

    # Готовый кэш остаётся снимком, обновление пересобирает текущий состав.
    def test_new_and_removed_reviews_between_runs(self):
        # Используем полный снимок двух отзывов.
        previous = {
            # Сохраняем идентификатор организации Яндекса.
            "organization_id": OID,
            # Сохраняем состояние поиска.
            "status": "complete",
            # Сохраняем поле company.
            "company": {"reviews_count": 2},
            # Сохраняем список отзывов.
            "reviews": [row(1), row(2)],
        }
        # Изолируем три последовательных состояния.
        with TemporaryDirectory() as folder:
            # CLI по умолчанию использует готовый снимок.
            target = Path(folder) / f"{OID}.json"
            # Сохраняем два прежних отзыва.
            save_result(target, previous)
            # Проверяем отсутствие обращения к браузеру без обновления.
            with patch.object(sys, "argv", ["collect_yandex.py", OID, "--output", folder]), patch(
                # Проверяем отсутствие обращения к браузеру без обновления.
                "collect_yandex.sync_playwright"
                # Проверяем отсутствие обращения к браузеру без обновления.
            ) as browser, patch("sys.stdout"):
                # Вызываем настоящий CLI готового кэша.
                main()
                # Новый отзыв без обновления не проверяется.
                browser.assert_not_called()
            # На сайте один отзыв удалён и один добавлен, счётчик остаётся прежним.
            page = fake_page(
                # На сайте один отзыв удалён и один добавлен, счётчик остаётся прежним.
                example_html(0, 2),
                # На сайте один отзыв удалён и один добавлен, счётчик остаётся прежним.
                {"company": {"reviews_count": 2}, "reviews": [row(2), row(3)]},
            )
            # Выполняем повторный сбор той же карточки.
            result = collect(page, OID, URL, target, None, 0)
            # Старый удалённый отзыв не должен вытеснить новый или завысить счётчик.
            self.assertEqual([r["author"] for r in result["reviews"]], ["3", "2"])
            # Обновлённый состав подтверждён целиком.
            self.assertEqual(result["status"], "complete")

    # Отказ открытия имеет конечное число повторов и отдельный статус.
    def test_network_failure_on_open_is_bounded(self):
        # Создаём страницу без доступного сетевого ответа.
        page = fake_page(example_html(), {})
        # Имитируем стандартную ошибку отключённой сети.
        page.goto = unittest.mock.Mock(side_effect=BrowserError("net::ERR_INTERNET_DISCONNECTED"))
        # Сетевые паузы измеряются по вызовам, а не выдерживаются в тесте.
        with TemporaryDirectory() as folder, patch("collect_yandex.time.sleep") as sleep:
            # Открытие должно завершиться после ограниченного числа попыток.
            result = collect(page, OID, URL, Path(folder) / "result.json", None, 0)
            # Не принимаем сетевой сбой за пустую или успешно прочитанную карточку.
            self.assertEqual(result["status"], "network_error")
            # Допускаются только три открытия.
            self.assertEqual(page.goto.call_count, 3)
            # Задержки возрастают, бесконечного повтора нет.
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [30, 60])

    # При сетевом отказе остаток очереди сохраняется для следующего запуска.
    def test_network_error_stops_queue(self):
        # Повторно используем совместимый искусственный итог.
        from test_resilience import outcome

        # Изолируем журнал очереди.
        with TemporaryDirectory() as folder, patch(
            # Изолируем журнал очереди.
            "collect_yandex.collect",
            # Изолируем журнал очереди.
            return_value=outcome(OID, "network_error")
            # Изолируем журнал очереди.
        ) as collector, patch("sys.stdout"):
            # В очереди есть ещё одна организация.
            jobs = [(OID, URL, Path(folder) / "one.json"), ("987", URL, Path(folder) / "two.json")]
            # Сетевой отказ первой карточки не должен начинать следующие обращения.
            outcomes = collect_many(
                # Сетевой отказ первой карточки не должен начинать следующие обращения.
                fake_page("", {}),
                # Сетевой отказ первой карточки не должен начинать следующие обращения.
                jobs,
                # Сетевой отказ первой карточки не должен начинать следующие обращения.
                None,
                # Отключаем повтор после 429 в автономной проверке.
                0,
                # Сетевой отказ первой карточки не должен начинать следующие обращения.
                10,
                # Отключаем повтор после 429 в автономной проверке.
                0,
                # Сетевой отказ первой карточки не должен начинать следующие обращения.
                "test",
                # Сетевой отказ первой карточки не должен начинать следующие обращения.
                Path(folder) / "jobs.json",
            )
            # Открыта только одна карточка.
            self.assertEqual(collector.call_count, 1)
            # Статус сетевого отказа сохраняется.
            self.assertEqual(outcomes[0]["status"], "network_error")


# Проверяем настоящий браузер без запросов к внешнему сайту.
class BrowserDeepTests(unittest.TestCase):
    # Один браузер обслуживает все локальные сценарии последовательно.
    @classmethod
    # Запускаем Chromium с явно указанным путём, если он задан.
    def setUpClass(cls):
        # Открываем управляющую сессию.
        cls.playwright = sync_playwright().start()
        # Используем установленный Chromium автономных тестов.
        cls.browser = cls.playwright.chromium.launch(
            # Используем установленный Chromium автономных тестов.
            executable_path=os.environ.get("REVIEW_CHROMIUM_PATH")
        )

    # Освобождаем браузер после класса.
    @classmethod
    # Закрываем собственные процессы даже после ошибок проверок.
    def tearDownClass(cls):
        # Закрываем Chromium.
        cls.browser.close()
        # Останавливаем управляющую сессию.
        cls.playwright.stop()

    # Повторное появление индикатора должно восстановить подгрузку после пустой порции.
    def test_visibility_retry_after_empty_batch(self):
        # Выполняем все изменения в локальном DOM.
        page = self.browser.new_page()
        # Исходный HTML сообщает четыре отзыва, но содержит только первые три.
        cards = example_html(4, 4).split("</script>", 1)[1].replace(" _collapsed", "")
        # Каждый отзыв занимает достаточно места для выхода индикатора за край панели.
        parts = cards.split('<div class="business-reviews-card-view__review">')[1:]
        # Раздельные карточки позволяют добавить четвёртую только при повторе видимости.
        parts = [
            # Раздельные карточки позволяют добавить четвёртую только при повторе видимости.
            '<div class="business-reviews-card-view__review" style="min-height:220px">' + part
            # Раздельные карточки позволяют добавить четвёртую только при повторе видимости.
            for part in parts
        ]
        # Публичный сборщик использует ту же прокручиваемую панель и индикатор.
        html = (
            # Публичный сборщик использует ту же прокручиваемую панель и индикатор.
            example_html(0, 4)
            # Публичный сборщик использует ту же прокручиваемую панель и индикатор.
            + '<div class="business-reviews-card-view" style="height:300px;overflow-y:auto">'
            # Публичный сборщик использует ту же прокручиваемую панель и индикатор.
            + "".join(parts[:3])
            # Публичный сборщик использует ту же прокручиваемую панель и индикатор.
            + '<div id="loading" class="business-reviews-card-view__skeleton" style="height:60px"></div></div>'
        )
        # Закрываем страницу при любом исходе.
        try:
            # Не выполняем никакой сетевой навигации.
            page.set_content(html)
            # Первый вход индикатора в окно возвращает пустую порцию, второй добавляет HTML.
            page.evaluate(
                """card => {
                  // Сохраняем число действительных появлений индикатора.
                  window.loads=0;
                  // Наблюдаем локальный индикатор в прокручиваемой панели.
                  const observer=new IntersectionObserver(entries=>{
                    // Уход из окна не является новой загрузкой.
                    if (!entries.some(e=>e.isIntersecting)) return;
                    // Имитируем пустой успешный ответ первой попытки.
                    window.loads++;
                    // Только повторное появление позволяет загрузить отсутствующий отзыв.
                    if (window.loads===2) {
                      // Добавляем целую карточку из локального HTML без изменения текста.
                      document.querySelector('#loading').insertAdjacentHTML('beforebegin',card);
                    }
                  // Ограничиваем наблюдение окном той же панели, что прокручивает сборщик.
                  },{root:document.querySelector('.business-reviews-card-view')});
                  // Подключаем обычное наблюдение видимости.
                  observer.observe(document.querySelector('#loading'));
                }""",
                # Четвёртый отзыв доступен только после повторного появления индикатора.
                parts[3],
            )
            # Подменяем только открытие и сокращаем ожидания автономного примера.
            proxy = fake_page(html, {})
            # Все чтения и прокрутки выполняются настоящим Chromium.
            proxy.evaluate = page.evaluate
            # Даём наблюдателю обработать отдельные изменения позиции.
            proxy.wait_for_timeout = lambda milliseconds: page.wait_for_timeout(100)
            # Первый пустой ответ воспроизводится настоящим ожиданием неизменного DOM.
            proxy.wait_for_function = lambda expression, **kwargs: page.wait_for_function(
                # Первый пустой ответ воспроизводится настоящим ожиданием неизменного DOM.
                expression, **dict(kwargs, timeout=300)
            )
            # Снимок сохраняется только во временной папке.
            with TemporaryDirectory() as folder:
                # Пустая первая порция не должна завершать весь сбор.
                result = collect(proxy, OID, URL, Path(folder) / "result.json", None, 0)
                # Второе появление индикатора даёт все четыре полные записи.
                self.assertEqual((result["status"], result["valid_reviews"]), ("complete", 4))
                # Новая загрузка действительно вызвана новым появлением индикатора.
                self.assertGreaterEqual(page.evaluate("window.loads"), 2)
        # Не оставляем наблюдателей после автономной проверки.
        finally:
            # Закрытие страницы освобождает весь локальный DOM.
            page.close()

    # Отключаем сеть после первой порции и продолжаем из сохранённого файла.
    def test_real_browser_offline_and_resume(self):
        # Отдельный контекст позволяет управлять отключением сети.
        context = self.browser.new_context()
        # Создаём страницу исключительно для вымышленной карточки.
        page = context.new_page()
        # Отдаём локальную разметку по вымышленному публичному адресу.
        page.route(
            # Отдаём локальную разметку по вымышленному публичному адресу.
            "**/*",
            # Отдаём локальную разметку по вымышленному публичному адресу.
            lambda route: route.fulfill(
                # Отдаём локальную разметку по вымышленному публичному адресу.
                status=200,
                # Отдаём локальную разметку по вымышленному публичному адресу.
                content_type="text/html",
                # Отдаём локальную разметку по вымышленному публичному адресу.
                body=example_html(2, 5),
            ),
        )
        # Первая навигация не обращается к интернету.
        page.goto(URL)
        # Последующие запросы будут остановлены именно отключением сети браузера.
        page.unroute("**/*")
        # Подменяем только открытие, чтение DOM выполняется настоящим Chromium.
        proxy = fake_page("", {})
        # Не выполняем внешнюю навигацию при начале сбора.
        proxy.goto = lambda *args, **kwargs: SimpleNamespace(status=200)
        # Используем фактическое состояние страницы.
        proxy.content = page.content
        # Регистрируем настоящие события сетевого отказа.
        proxy.on = page.on
        # Удаляем обработчики между попытками.
        proxy.remove_listener = page.remove_listener
        # Даём отключённому запросу породить браузерное событие.
        proxy.wait_for_timeout = lambda milliseconds: page.wait_for_timeout(100)
        # Три отказа ожидания не должны задерживать автономную проверку на полминуты.
        proxy.wait_for_function = unittest.mock.Mock(side_effect=BrowserTimeout("no new reviews"))

        # Первое действие прокрутки отключает сеть и запускает обычную HTML-загрузку.
        def evaluate(expression, arg):
            # Имитируем обрыв ровно посреди подгрузки.
            if "scrollIntoView" in expression:
                # Chromium сам воспроизводит недоступность сети.
                context.set_offline(True)
                # Запрос HTML не является обращением к сервисному API.
                return page.evaluate("() => {fetch('/maps/offline-check.html').catch(()=>{});}")
            # Извлечение и раскрытие остаются настоящими браузерными действиями.
            return page.evaluate(expression, arg)

        # Подключаем управляемое отключение к настоящему чтению DOM.
        proxy.evaluate = evaluate
        # Все результаты остаются во временной папке.
        try:
            # Изолируем частичный и восстановленный снимки.
            with TemporaryDirectory() as folder:
                # Один файл используется обеими попытками.
                target = Path(folder) / "result.json"
                # Выполняем сбор первых двух записей и обрыв подгрузки.
                partial = collect(proxy, OID, URL, target, None, 0)
                # Отказ без HTTP-ответа должен быть явно отличим от конца выдачи.
                self.assertEqual(partial["status"], "network_error")
                # Уже прочитанные полные тексты сохранены.
                self.assertEqual(partial["valid_reviews"], 2)
                # Реальный Chromium сообщил отключение сети.
                self.assertTrue(
                    # Реальный Chromium сообщил отключение сети.
                    any(
                        # Реальный Chromium сообщил отключение сети.
                        "ERR_INTERNET_DISCONNECTED" in r["error"]
                        # Реальный Chromium сообщил отключение сети.
                        for r in partial["network"]["failures"]
                    )
                )
                # Восстанавливаем сеть, продолжая использовать локальные подмены.
                context.set_offline(False)
                # Новое состояние доступной страницы содержит все пять отзывов.
                page.set_content(example_html(5, 5))
                # Полный повтор уже не требует прокрутки или сетевых загрузок.
                proxy.evaluate = page.evaluate
                # Продолжаем из того же сохранённого снимка.
                restored = collect(proxy, OID, URL, target, None, 0)
                # Все пять текстов подтверждены без дубликатов.
                self.assertEqual((restored["status"], restored["valid_reviews"]), ("complete", 5))
                # Завершённый файл проходит обычную проверку кэша.
                self.assertTrue(cache_complete(restored, OID, None))
        # Не оставляем контекст с отключённой сетью после теста.
        finally:
            # Освобождаем только собственную вымышленную страницу.
            context.close()

    # Уже выбранная подпись не заменяет проверку фактического порядка дат.
    def test_selected_newest_still_checks_dates(self):
        # Используем локальную страницу без сети.
        page = self.browser.new_page()
        # Готовим подпись, при которой прежний код возвращался слишком рано.
        page.set_content(
            # Готовим подпись, при которой прежний код возвращался слишком рано.
            '<div class="business-reviews-card-view__ranking"><div class="rating-ranking-view">По новизне</div></div>'
        )
        # Проверяем путь подтверждения без двадцатисекундного ожидания пустой выдачи.
        try:
            # Ожидание дат имитирует отсутствие корректного порядка.
            with patch.object(
                # Ожидание дат имитирует отсутствие корректного порядка.
                page,
                # Ожидание дат имитирует отсутствие корректного порядка.
                "wait_for_function",
                # Ожидание дат имитирует отсутствие корректного порядка.
                side_effect=BrowserTimeout("dates unavailable")
                # Ожидание дат имитирует отсутствие корректного порядка.
            ) as wait:
                # Подтверждение должно отказать даже при готовой подписи меню.
                with self.assertRaises(BrowserTimeout):
                    # Повторно проверяем фактическую выдачу.
                    select_newest(page)
                # Проверка дат действительно была запрошена.
                wait.assert_called_once()
        # Закрываем страницу этой проверки.
        finally:
            # Общий браузер остаётся для остальных сценариев.
            page.close()


# Разрешаем самостоятельный запуск новых проверок.
if __name__ == "__main__":
    # Выполняем все автономные сценарии этого файла.
    unittest.main()
