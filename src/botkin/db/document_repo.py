"""Репозиторий документов: CRUD, статусы, поиск, статистика.

Методы разнесены по mixin-модулям (document_read, document_write);
этот файл сохраняет единый класс DocumentRepo для обратной совместимости.
"""
from __future__ import annotations

from .base import BaseRepo
from .document_read import DocumentReadMixin
from .document_write import DocumentWriteMixin


class DocumentRepo(DocumentReadMixin, DocumentWriteMixin, BaseRepo):
    table = "documents"
