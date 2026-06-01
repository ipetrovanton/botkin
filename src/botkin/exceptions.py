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
    """Сбой извлечения данных."""


class LLMError(BotkinError):
    """Ошибка вызова LLM/VLM."""


class DatabaseError(BotkinError):
    """Ошибка работы с БД."""