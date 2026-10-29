"""Проверяем подготовку браузера без сетевых запросов."""

# Создаём контролируемые ошибки установки.
import subprocess

# Проверяем запуск установщика тем же Python.
import sys

# Используем стандартный набор проверок и подменяем внешние операции.
import unittest

# Исключаем скачивание и настоящий запуск браузера в модульных проверках.
from unittest.mock import Mock, patch

# Проверяем общий путь запуска для поиска и отзывов.
from browser_setup import BrowserError, _in_colab, launch_chromium


# Проверяем готовый браузер, установку отсутствующего и отказы.
class BrowserSetupTests(unittest.TestCase):
    # Фиксируем обычную среду для проверок, не зависящих от Colab.
    def setUp(self):
        # В этих сценариях системные библиотеки не устанавливаются автоматически.
        environment = patch("browser_setup._in_colab", return_value=False)
        # Подменяем определение среды до выполнения проверки.
        environment.start()
        # Возвращаем определение среды после каждой проверки.
        self.addCleanup(environment.stop)

    # В Colab подготавливаем также системные библиотеки Linux.
    def test_colab_prepares_system_dependencies(self):
        # Проверяем отсутствующий браузер и два вида отказа библиотек.
        for message in (
            # Проверяем отсутствующий браузер и два вида отказа библиотек.
            "Executable doesn't exist at /missing/chromium",
            # Проверяем отсутствующий браузер и два вида отказа библиотек.
            "Host system is missing dependencies to run browsers",
            # Проверяем отсутствующий браузер и два вида отказа библиотек.
            "error while loading shared libraries: libnss3.so",
        ):
            # Отдельно проверяем каждый вариант начального отказа.
            with self.subTest(message=message):
                # Следующий запуск возвращает браузер после установки.
                playwright = Mock()
                # Первый запуск сообщает о недостающем компоненте.
                playwright.chromium.launch.side_effect = [BrowserError(message), "browser"]
                # Определяем среду Colab без скачивания и установки пакетов Linux.
                with patch("browser_setup._in_colab", return_value=True), patch(
                    # Определяем среду Colab без скачивания и установки пакетов Linux.
                    "browser_setup.subprocess.run"
                # Определяем среду Colab без скачивания и установки пакетов Linux.
                ) as install:
                    # После подготовки запуск успешен.
                    self.assertEqual(launch_chromium(playwright), "browser")
                    # Установщик получает запрос системных зависимостей.
                    install.assert_called_once_with(
                        # Установщик получает запрос системных зависимостей.
                        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
                        # Установщик получает запрос системных зависимостей.
                        check=True,
                        # Установщик получает запрос системных зависимостей.
                        timeout=600,
                    )

    # Определение Colab не импортирует компоненты среды ноутбука.
    def test_colab_detection(self):
        # Наличие google.colab значимо только в Linux.
        with patch("browser_setup.sys.platform", "win32"), patch(
            # Наличие google.colab значимо только в Linux.
            "browser_setup.find_spec"
        # Наличие google.colab значимо только в Linux.
        ) as find:
            # На Windows автоматическая установка системных пакетов выключена.
            self.assertFalse(_in_colab())
            # Проверка модуля даже не выполняется.
            find.assert_not_called()
        # В Linux проверяем найденный модуль, отсутствие и отсутствующий пакет google.
        for answer in (Mock(), None, ModuleNotFoundError("google")):
            # Указываем Linux без изменения реальной операционной системы.
            with patch("browser_setup.sys.platform", "linux"), patch(
                # Указываем Linux без изменения реальной операционной системы.
                "browser_setup.find_spec",
                # Указываем Linux без изменения реальной операционной системы.
                side_effect=answer if isinstance(answer, Exception) else None,
                # Указываем Linux без изменения реальной операционной системы.
                return_value=answer,
            # Указываем Linux без изменения реальной операционной системы.
            ) as find:
                # Только найденный модуль означает среду Colab.
                self.assertEqual(
                    # Только найденный модуль означает среду Colab.
                    _in_colab(), answer is not None and not isinstance(answer, Exception)
                )
                # Проверяем именно google.colab, а не любые библиотеки Google.
                find.assert_called_once_with("google.colab")

    # Готовый Chromium не требует установщика.
    @patch("browser_setup.subprocess.run")
    # Проверяем запуск уже установленного браузера.
    def test_ready_browser(self, install):
        # Создаём объект Playwright с проверяемым вызовом запуска.
        playwright = Mock()
        # Возвращаем тот же браузер, который открыл Playwright.
        self.assertIs(launch_chromium(playwright), playwright.chromium.launch.return_value)
        # Запуск выполняется один раз без окна.
        playwright.chromium.launch.assert_called_once_with(headless=True)
        # Повторное использование браузера ничего не скачивает.
        install.assert_not_called()

    # Отсутствующую совместимую сборку устанавливаем ровно один раз.
    @patch("browser_setup.subprocess.run")
    # Проверяем установку и однократный повтор запуска.
    def test_missing_browser(self, install):
        # Создаём контролируемый запуск браузера.
        playwright = Mock()
        # Первая попытка обнаруживает отсутствие файла, вторая успешна.
        playwright.chromium.launch.side_effect = [
            # Первая попытка обнаруживает отсутствие файла, вторая успешна.
            BrowserError("Executable doesn't exist at /missing/chromium"),
            # Первая попытка обнаруживает отсутствие файла, вторая успешна.
            "browser",
        ]
        # После установки возвращаем браузер.
        self.assertEqual(launch_chromium(playwright), "browser")
        # Установщик получает то же окружение Python и ограничение времени.
        install.assert_called_once_with(
            # Установщик получает то же окружение Python и ограничение времени.
            [sys.executable, "-m", "playwright", "install", "chromium"],
            # Установщик получает то же окружение Python и ограничение времени.
            check=True,
            # Установщик получает то же окружение Python и ограничение времени.
            timeout=600,
        )
        # После установки делаем одну повторную попытку.
        self.assertEqual(playwright.chromium.launch.call_count, 2)

    # Указанный пользователем браузер нельзя незаметно заменять другим.
    @patch("browser_setup.subprocess.run")
    # Проверяем приоритет явно заданного исполняемого файла.
    def test_explicit_browser_error(self, install):
        # Создаём запуск с отсутствующим явно заданным файлом.
        playwright = Mock()
        # Передаём исходную ошибку запуска.
        playwright.chromium.launch.side_effect = BrowserError(
            # Передаём исходную ошибку запуска.
            "Executable doesn't exist at custom.exe"
        )
        # Сохраняем отказ заданного браузера.
        with self.assertRaises(BrowserError):
            # Явный путь имеет приоритет.
            launch_chromium(playwright, "custom.exe")
        # Путь остаётся неизменным.
        playwright.chromium.launch.assert_called_once_with(
            # Путь остаётся неизменным.
            headless=True,
            # Путь остаётся неизменным.
            executable_path="custom.exe",
        )
        # Автоматическая установка отключена.
        install.assert_not_called()

    # Системная ошибка запуска не означает отсутствие браузера.
    @patch("browser_setup.subprocess.run")
    # Проверяем передачу системного отказа без скачивания.
    def test_other_launch_error(self, install):
        # Создаём запуск с отказом из-за системной библиотеки.
        playwright = Mock()
        # Моделируем ошибку, которую повторное скачивание не исправит.
        playwright.chromium.launch.side_effect = BrowserError("Missing system libraries")
        # Передаём исходную причину без замены.
        with self.assertRaisesRegex(BrowserError, "Missing system libraries"):
            # Пробуем обычный запуск.
            launch_chromium(playwright)
        # Не скачиваем браузер при нерелевантной ошибке.
        install.assert_not_called()

    # Неудачная установка завершает запуск с понятной причиной.
    def test_installation_failures(self):
        # Проверяем отказ установщика, истечение срока и отсутствие доступа.
        for failure in (
            # Проверяем отказ установщика, истечение срока и отсутствие доступа.
            subprocess.CalledProcessError(1, "installer"),
            # Проверяем отказ установщика, истечение срока и отсутствие доступа.
            subprocess.TimeoutExpired("installer", 600),
            # Проверяем отказ установщика, истечение срока и отсутствие доступа.
            OSError("Denied"),
        ):
            # Отдельно проверяем каждый тип отказа.
            with self.subTest(failure=type(failure).__name__):
                # Запускаем браузер с отсутствующей сборкой.
                playwright = Mock()
                # Начальный отказ требует установки.
                playwright.chromium.launch.side_effect = BrowserError(
                    # Начальный отказ требует установки.
                    "Executable doesn't exist at /missing/chromium"
                )
                # Установщик завершается заданным отказом.
                with patch("browser_setup.subprocess.run", side_effect=failure) as install:
                    # Ошибка поясняет, как повторить запуск.
                    with self.assertRaisesRegex(
                        # Ошибка поясняет, как повторить запуск.
                        RuntimeError,
                        # Ошибка поясняет, как повторить запуск.
                        "Не удалось автоматически установить Chromium"
                        # Ошибка поясняет, как повторить запуск.
                    ) as caught:
                        # Выполняем проверяемый путь подготовки.
                        launch_chromium(playwright)
                    # Сохраняем исходную причину для диагностики.
                    self.assertIs(caught.exception.__cause__, failure)
                    # Установка выполняется один раз.
                    install.assert_called_once()
                    # Не запускаем браузер после неудачной установки.
                    self.assertEqual(playwright.chromium.launch.call_count, 1)


# Позволяем выполнить проверки отдельно.
if __name__ == "__main__":
    # Запускаем стандартный тестовый исполнитель.
    unittest.main()
