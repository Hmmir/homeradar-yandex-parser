"""Запуск совместимого Chromium с установкой отсутствующего браузера."""

# Проверяем наличие модуля Colab без импорта его служебных компонентов.
from importlib.util import find_spec

# Запускаем официальный установщик браузеров отдельным процессом.
import subprocess

# Используем то же окружение Python, в котором установлен пакет.
import sys

# Различаем отсутствие браузера и остальные ошибки запуска.
from playwright.sync_api import Error as BrowserError


# Отличаем среду Colab от обычной установки Linux.
def _in_colab():
    # На Windows и macOS подготовка системных библиотек Colab не нужна.
    if sys.platform != "linux":
        # Сохраняем обычный способ установки браузера.
        return False
    # В процессе сборщика также доступен установленный пакет google.colab.
    try:
        # Поиск модуля не запускает среду ноутбука.
        return find_spec("google.colab") is not None
    # В обычном Python может отсутствовать даже родительский пакет google.
    except ModuleNotFoundError:
        # Не устанавливаем системные библиотеки автоматически вне Colab.
        return False


# Одинаково подготавливаем браузер для поиска организаций и сбора отзывов.
def launch_chromium(playwright, executable=None):
    """Устанавливаем Chromium при отсутствии, затем запускаем без окна."""
    # Обычный сбор выполняется без видимого окна браузера.
    options = {"headless": True}
    # Явно заданный браузер имеет приоритет над автоматической установкой.
    if executable:
        # Передаём путь пользователя без подмены браузера.
        options["executable_path"] = str(executable)
    # Готовый браузер запускается сразу без вызова установщика.
    try:
        # Playwright сам выбирает совместимый Chromium или его headless shell.
        return playwright.chromium.launch(**options)
    # Устанавливаем только отсутствующую сборку, сохраняя остальные ошибки.
    except BrowserError as error:
        # В Colab разрешаем подготовить также отсутствующие системные библиотеки.
        in_colab = _in_colab()
        # Обычный случай: нужный браузер ещё не скачан.
        missing_browser = "Executable doesn't exist at" in str(error)
        # Уже скачанный Chromium также может требовать библиотек Linux.
        missing_libraries = in_colab and any(
            # Уже скачанный Chromium также может требовать библиотек Linux.
            marker in str(error)
            # Уже скачанный Chromium также может требовать библиотек Linux.
            for marker in (
                # Уже скачанный Chromium также может требовать библиотек Linux.
                "Host system is missing dependencies",
                # Уже скачанный Chromium также может требовать библиотек Linux.
                "error while loading shared libraries",
            )
        )
        # Явный путь пользователя и другие ошибки не требуют переустановки.
        if executable or not (missing_browser or missing_libraries):
            # Передаём исходную причину вызывающему коду.
            raise
    # Объясняем пользователю загрузку при первом запуске.
    print(
        # Сообщаем о загрузке отсутствующей совместимой сборки.
        "Подготавливаем совместимый Chromium для Playwright и необходимые библиотеки.",
        # Сразу показываем состояние задачи в терминале.
        flush=True,
    )
    # Ограничиваем длительность установки и явно обрабатываем её отказ.
    try:
        # Официальный установщик выбирает браузер для установленной версии Playwright.
        command = [sys.executable, "-m", "playwright", "install", "chromium"]
        # В Colab добавляем системные библиотеки без отдельной команды пользователя.
        if in_colab:
            # Используем штатную установку зависимостей Chromium в Linux.
            command.append("--with-deps")
        # Установщик проверяет нужную версию и использует стандартный кэш Playwright.
        subprocess.run(
            # Вызываем установленный Playwright тем же Python; ждём не более десяти минут.
            command,
            # Установщик проверяет нужную версию и использует стандартный кэш Playwright.
            check=True,
            # Установщик проверяет нужную версию и использует стандартный кэш Playwright.
            timeout=600,
        )
    # Ошибка скачивания или доступа не должна превращаться в бесконечные повторы.
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # При следующем запуске пользователь сможет повторить установку.
        raise RuntimeError(
            # Подсказываем, что проверить перед повторным запуском.
            "Не удалось автоматически установить Chromium. Проверьте интернет и доступ к папке браузеров, затем повторите запуск."
            # Сохраняем исходную причину отказа для диагностики.
        ) from error
    # После успешной установки выполняем ровно одну повторную попытку запуска.
    return playwright.chromium.launch(**options)
