"""Учёт незавершённых загрузок по метаданным браузера, без чтения ответов."""

# Разбираем домен и путь без сохранения параметров запроса.
from urllib.parse import urlsplit


# Учитываем только документы и динамические загрузки самой страницы карт.
def relevant(request, url=""):
    # Не включаем изображения, тайлы и стороннюю аналитику в ожидание данных.
    parsed = urlsplit(getattr(request, "url", url))
    # Проверяем источник и тип загрузки, которую выполняет обычный браузер.
    return (
        # Служебные ресурсы сторонних доменов не определяют завершение чтения.
        parsed.hostname in {"yandex.ru", "yandex.com"}
        # Ограничиваем наблюдение текущим приложением карт.
        and parsed.path.startswith("/maps/")
        # Учитываем HTML и динамические запросы страницы.
        and request.resource_type in {"document", "xhr", "fetch"}
    )


# Объединяем одинаковый учёт загрузок для поиска и чтения отзывов.
class PageLoads:
    # Подключаем наблюдение только на время обработки одной страницы.
    def __init__(self, page, network):
        # Сохраняем страницу для последующего снятия обработчиков.
        self.page = page
        # Используем общий журнал сетевых событий текущей попытки.
        self.network = network
        # Сохраняем только метаданные незавершённых запросов.
        self.pending = {}
        # Отдельно считаем серверные отказы необходимых загрузок.
        self.server_errors = 0
        # В поисковых снимках прежних версий поле могло отсутствовать.
        network.setdefault("failures", [])
        # Фиксируем начало загрузки, завершение, отказ и код ответа.
        self.handlers = {
            # Начало не означает появление новых записей.
            "request": self.start,
            # Завершение HTTP-загрузки снимает признак ожидания.
            "requestfinished": self.finish,
            # Обрыв сети тоже завершает конкретный запрос.
            "requestfailed": self.fail,
            # Читаем только статус ответа, без тела и параметров.
            "response": self.response,
        }
        # Подключаем обработчики обычных событий браузера.
        for event, handler in self.handlers.items():
            # Наблюдение не создаёт дополнительных запросов.
            page.on(event, handler)

    # Запоминаем незавершённую необходимую загрузку.
    def start(self, request):
        # Пропускаем необязательные ресурсы страницы.
        if relevant(request):
            # Убираем параметры URL из диагностики.
            parsed = urlsplit(request.url)
            # Уникальность запроса определяется объектом браузера, а не адресом.
            self.pending[id(request)] = {
                # Записываем только домен источника.
                "host": parsed.hostname,
                # Записываем путь без содержимого запроса.
                "path": parsed.path,
                # Записываем тип загрузки.
                "resource_type": request.resource_type,
            }

    # Снимаем признак ожидания после завершения запроса.
    def finish(self, request):
        # Повторное уведомление не является ошибкой.
        self.pending.pop(id(request), None)

    # Сохраняем отказ необходимой загрузки.
    def fail(self, request):
        # Ошибка завершает запрос так же, как успешный ответ.
        self.finish(request)
        # Смена страницы или сортировки может штатно отменить прежний запрос.
        if relevant(request) and request.failure != "net::ERR_ABORTED":
            # Получаем путь без параметров запроса.
            parsed = urlsplit(request.url)
            # Сохраняем только метаданные сетевого отказа.
            self.network["failures"].append(
                {
                    # Записываем домен источника.
                    "host": parsed.hostname,
                    # Записываем путь без параметров.
                    "path": parsed.path,
                    # Записываем тип загрузки.
                    "resource_type": request.resource_type,
                    # Записываем причину отказа браузера.
                    "error": request.failure,
                }
            )

    # Отличаем HTTP-ошибку сервера от успешно загруженного пустого списка.
    def response(self, response):
        # Коды сторонних ресурсов не определяют полноту данных.
        if response.status >= 500 and relevant(response.request, response.url):
            # Учитываем серверный отказ для следующей проверки остановки.
            self.server_errors += 1

    # Запоминаем состояние ошибок при подтверждённом прогрессе.
    def marker(self):
        # Новый отзыв, а не HTTP 200, определяет начало следующего интервала.
        return len(self.network["failures"]), self.server_errors

    # Выбираем причину отсутствия данных после ограниченного числа ожиданий.
    def stalled_status(self, marker, fallback):
        # Сетевой отказ имеет приоритет перед предположением о конце списка.
        if len(self.network["failures"]) > marker[0]:
            # Отказ не является успешным пустым ответом.
            return "network_error"
        # Сервер мог вернуть HTTP-ответ без необходимых данных.
        if self.server_errors > marker[1]:
            # Сохраняем возможность повторить незавершённый сбор.
            return "server_error"
        # Не считаем ещё выполняющуюся загрузку исчерпанным списком.
        return "load_timeout" if self.pending else fallback

    # Добавляем текущую диагностику ожидания в сохранённый результат.
    def checkpoint(self):
        # Число отражает состояние браузера в момент сохранения.
        self.network["pending_loads"] = len(self.pending)
        # Ограничиваем объём диагностики, не отменяя остальные загрузки.
        self.network["pending_examples"] = list(self.pending.values())[:10]

    # Снимаем обработчики перед переходом к следующей организации.
    def close(self):
        # Не переносим события прежней страницы в следующий результат.
        for event, handler in self.handlers.items():
            # Используем те же объекты обработчиков, которые были подключены.
            self.page.remove_listener(event, handler)
