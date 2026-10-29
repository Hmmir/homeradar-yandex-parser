"""Извлечение первой порции отзывов из HTML страницы без подключения к сети."""

# Подключаем разбор параметров запуска.
import argparse

# Подключаем чтение и запись JSON.
import json

# Подключаем разбор HTML средствами стандартной библиотеки.
from html.parser import HTMLParser

# Подключаем работу с путями и файлами.
from pathlib import Path


# Объединяем обработчики чтения HTML.
class StateScript(HTMLParser):
    # Подготавливаем накопитель JSON из HTML-тега script.
    def __init__(self):
        # Инициализируем стандартный HTML-разборщик.
        super().__init__()
        # Переключаем накопление текста встроенного JSON.
        self.capture = False
        # Подготавливаем список фрагментов JSON.
        self.parts = []

    # Начинаем читать только script с состоянием публичной страницы.
    def handle_starttag(self, tag, attrs):
        # Преобразуем HTML-атрибуты в словарь для поиска нужного script.
        attrs = dict(attrs)
        # Проверяем связанные условия перед продолжением обработки.
        if (
            # Выбираем только HTML-тег script.
            tag == "script"
            # Проверяем класс, содержащий состояние страницы.
            and "state-view" in attrs.get("class", "").split()
            # Принимаем только встроенный JSON, а не исполняемый сценарий.
            and attrs.get("type") == "application/json"
        ):
            # Переключаем накопление текста встроенного JSON.
            self.capture = True

    # Заканчиваем чтение состояния при закрытии script.
    def handle_endtag(self, tag):
        # Проверяем HTML-тег и атрибуты выбранного script.
        if tag == "script":
            # Переключаем накопление текста встроенного JSON.
            self.capture = False

    # Добавляем содержимое выбранного script к накопленным фрагментам.
    def handle_data(self, data):
        # Читаем данные только внутри выбранного script.
        if self.capture:
            # Накапливаем фрагменты встроенного JSON.
            self.parts.append(data)


# Извлекаем сведения об организации и первую порцию отзывов из HTML.
def extract(html, oid):
    # Создаём разборщик HTML или параметров командной строки.
    parser = StateScript()
    # Передаём HTML стандартному разборщику.
    parser.feed(html)
    # Разбираем встроенный JSON состояния публичной страницы.
    state = json.loads("".join(parser.parts))
    # Собираем организации из открытых панелей страницы.
    items = [
        # Добавляем найденную организацию в общий список.
        item
        # Последовательно обрабатываем записи указанного набора.
        for panel in state.get("stack", [])
        # Последовательно обрабатываем записи указанного набора.
        for item in panel.get("results", {}).get("items", [])
    ]
    # Выбираем организацию с заданным идентификатором.
    organization = next(item for item in items if str(item.get("id")) == oid)
    # Получаем общие оценки и счётчики организации.
    ratings = organization.get("ratingData", {})
    # Подготавливаем результат обработки и показатели полноты.
    result = {
        # Сохраняем идентификатор организации.
        "organization_id": oid,
        # Сохраняем сведения об организации.
        "company": {
            # Сохраняем название организации.
            "name": organization.get("title"),
            # Сохраняем адрес организации.
            "address": organization.get("address"),
            # Сохраняем координаты в порядке долгота, широта.
            "coordinates_lon_lat": organization.get("coordinates"),
            # Сохраняем категории организации.
            "categories": organization.get("categories"),
            # Сохраняем общую оценку организации.
            "rating": ratings.get("ratingValue"),
            # Сохраняем число всех оценок организации.
            "ratings_count": ratings.get("ratingCount"),
            # Сохраняем число отзывов с текстом на карточке.
            "reviews_count": ratings.get("reviewCount"),
        },
        # Сохраняем список отзывов.
        "reviews": [],
    }
    # Последовательно обрабатываем записи указанного набора.
    for item in organization.get("reviewResults", {}).get("reviews", []):
        # Исключаем отзывы, относящиеся к другой организации.
        if str(item.get("businessId")) != oid:
            # Переходим к следующей попытке или записи.
            continue
        # Проверяем оценку отдельного отзыва перед сохранением.
        rating = item.get("rating")
        # Обновляем список сохранённых отзывов.
        result["reviews"].append(
            {
                # Сохраняем идентификатор записи.
                "id": item.get("reviewId"),
                # Сохраняем отображаемое имя автора.
                "author": item.get("author", {}).get("name"),
                # Сохраняем публичный идентификатор автора.
                "author_public_id": item.get("author", {}).get("publicId"),
                # Сохраняем дату отзыва.
                "date": item.get("updatedTime"),
                # Сохраняем текст отзыва.
                "text": item.get("text"),
                # Сохраняем оценку отдельного отзыва.
                "rating": rating
                # Проверяем допустимость оценки отдельного отзыва.
                if type(rating) is int and 1 <= rating <= 5
                # Если оценка недопустима, явно обозначаем её отсутствие.
                else None,
                # Сохраняем язык отзыва.
                "text_language": item.get("textLanguage"),
            }
        )
    # Считаем отзывы, действительно встроенные в HTML страницы.
    result["loaded_reviews"] = len(result["reviews"])
    # Записываем причину завершения текущего этапа.
    result["status"] = (
        # Определяем итоговый статус с учётом полноты обязательных полей.
        "complete"
        # Сопоставляем размер встроенной порции со счётчиком всех отзывов.
        if result["loaded_reviews"] == ratings.get("reviewCount")
        # Отмечаем, что HTML содержит только первую порцию отзывов.
        else "first_batch_only"
    )
    # Возвращаем результат; блок finally перед этим сохранит прогресс.
    return result


# Выполняем запуск только при прямом вызове файла.
if __name__ == "__main__":
    # Создаём разборщик HTML или параметров командной строки.
    parser = argparse.ArgumentParser(description=__doc__)
    # Добавляем параметр командной строки с описанием.
    parser.add_argument("html", type=Path)
    # Добавляем параметр командной строки с описанием.
    parser.add_argument("organization_id")
    # Добавляем параметр командной строки с описанием.
    parser.add_argument("--output", type=Path, required=True)
    # Получаем значения параметров командной строки.
    args = parser.parse_args()
    # Подготавливаем результат обработки и показатели полноты.
    result = extract(args.html.read_text(encoding="utf-8"), args.organization_id)
    # Создаём папку для итогового JSON.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Записываем извлечённые данные в UTF-8.
    args.output.write_text(
        # Сохраняем кириллицу без преобразования в последовательности Unicode.
        json.dumps(result, ensure_ascii=False, indent=2),
        # Записываем файл в UTF-8 для сохранения кириллицы.
        encoding="utf-8",
    )
    # Выводим краткое событие или результат в терминал.
    print(
        # Преобразуем данные в JSON с заданными параметрами записи.
        json.dumps(
            # Сохраняем кириллицу без преобразования в последовательности Unicode.
            {k: v for k, v in result.items() if k != "reviews"},
            # Сохраняем кириллицу в JSON без Unicode-последовательностей.
            ensure_ascii=False,
        )
    )
