"""Кастомные исключения приложения."""


class BotkinError(Exception):
    """Базовое исключение."""


class ConfigurationError(BotkinError):
    """Ошибка конфигурации."""


class DocumentError(BotkinError):
    """Ошибка обработки документа."""


class ClassificationError(DocumentError):
    """Сбой классификации документа."""


class ExtractionError(DocumentError):
    """Сбой извлечения данных.

    raw_text — сырой ответ модели на момент сбоя (если доступен): из него можно
    спасти полные объекты-строки при обрезанном JSON (см. extract._salvage_json_objects).
    """
    raw_text: str | None = None


class LLMError(BotkinError):
    """Ошибка вызова LLM/VLM."""


class DatabaseError(BotkinError):
    """Ошибка работы с БД."""