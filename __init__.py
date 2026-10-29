"""Сбор организаций, оценок и отзывов по выбранной территории."""

# Предоставляем один интерфейс для обычного Python и файлов Jupyter.
from .api import CollectionResult, collect_area, list_categories, list_cities, load_results

# Явно перечисляем доступные пользователю функции и тип результата.
__all__ = ["CollectionResult", "collect_area", "list_categories", "list_cities", "load_results"]
