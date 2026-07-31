"""Хранилище оригиналов документов: локальный диск (по умолчанию) или MinIO (S3).

URI-схема в documents.source_path:
- локальный бэкенд — абсолютный путь файла (обратная совместимость со старыми записями);
- MinIO — `minio://<bucket>/<key>`;
- `manual://...` — служебные документы без файла (ручной ввод), хранилище их игнорирует.

Версионирование:
- MinIO: нативный bucket versioning (включается при первом обращении);
- локально: при замене старая копия уезжает в `<dir>/.versions/<имя>.v<N>.<ts>`.

Пайплайн (pymupdf/PIL) читает файлы с диска, поэтому у обоих бэкендов есть
`open_local(uri) -> Path`: для MinIO объект скачивается в локальный кэш.
"""
import hashlib
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from botkin.config import (
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
    STORAGE_BACKEND,
    UPLOAD_SOURCES_DIR,
)

log = logging.getLogger(__name__)

_MINIO_SCHEME = "minio://"
_MANUAL_SCHEME = "manual://"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timestamped_key(user_id: int, safe_name: str) -> str:
    """Ключ вида `<user>/<YYYY-MM>/<ts>-<имя>` — одинаковый для обоих бэкендов."""
    now = datetime.now(timezone.utc)
    return f"{user_id}/{now.strftime('%Y-%m')}/{now.strftime('%Y%m%dT%H%M%S')}-{safe_name}"


class LocalStorage:
    """Файлы на локальном диске под UPLOAD_SOURCES_DIR."""

    def save(self, user_id: int, safe_name: str, data: bytes) -> str:
        dest = UPLOAD_SOURCES_DIR / _timestamped_key(user_id, safe_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return str(dest)

    def open_local(self, uri: str) -> Path | None:
        path = Path(uri)
        return path if path.is_file() else None

    def delete(self, uri: str) -> None:
        path = Path(uri)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        # Версии удаляются вместе с оригиналом — иначе «удалённый» документ
        # продолжит жить в .versions.
        versions_dir = path.parent / ".versions"
        if versions_dir.is_dir():
            for old in versions_dir.glob(f"{path.name}.v*"):
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass

    def replace(self, uri: str, data: bytes) -> None:
        """Замена содержимого с сохранением старой копии в .versions."""
        path = Path(uri)
        if path.is_file():
            versions_dir = path.parent / ".versions"
            versions_dir.mkdir(exist_ok=True)
            n = len(list(versions_dir.glob(f"{path.name}.v*"))) + 1
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            shutil.copy2(path, versions_dir / f"{path.name}.v{n}.{ts}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        # Валидация записи: перечитываем и сверяем хеш — гарантия целостности замены.
        if sha256_of(path.read_bytes()) != sha256_of(data):
            raise IOError(f"Целостность записи нарушена: {path}")

    def versions(self, uri: str) -> list[dict]:
        path = Path(uri)
        items: list[dict] = []
        if path.is_file():
            stat = path.stat()
            items.append({
                "version_id": "current", "is_current": True,
                "size": stat.st_size,
                "last_modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        versions_dir = path.parent / ".versions"
        if versions_dir.is_dir():
            for old in sorted(versions_dir.glob(f"{path.name}.v*"), reverse=True):
                stat = old.stat()
                items.append({
                    "version_id": old.name.rsplit(".", 2)[-2], "is_current": False,
                    "size": stat.st_size,
                    "last_modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc).isoformat(),
                })
        return items


class MinioStorage:
    """Оригиналы в MinIO (S3 API) с нативным версионированием бакета."""

    def __init__(self) -> None:
        self._client = None
        # Кэш скачанных объектов: пайплайн и просмотр оригинала читают Path с диска.
        self._cache_dir = Path(tempfile.gettempdir()) / "botkin-minio-cache"

    @property
    def client(self) -> object:
        # Ленивая инициализация: пакет minio нужен только при backend=minio.
        if self._client is None:
            from minio import Minio
            from minio.versioningconfig import ENABLED, VersioningConfig

            self._client = Minio(
                MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE,
            )
            if not self._client.bucket_exists(MINIO_BUCKET):
                self._client.make_bucket(MINIO_BUCKET)
            self._client.set_bucket_versioning(MINIO_BUCKET, VersioningConfig(ENABLED))
        return self._client

    @staticmethod
    def _key(uri: str) -> str:
        return uri[len(_MINIO_SCHEME):].split("/", 1)[1]

    def _put(self, key: str, data: bytes) -> None:
        import io

        self.client.put_object(MINIO_BUCKET, key, io.BytesIO(data), len(data))

    def save(self, user_id: int, safe_name: str, data: bytes) -> str:
        key = _timestamped_key(user_id, safe_name)
        self._put(key, data)
        return f"{_MINIO_SCHEME}{MINIO_BUCKET}/{key}"

    def open_local(self, uri: str) -> Path | None:
        key = self._key(uri)
        suffix = Path(key).suffix
        cached = self._cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()}{suffix}"
        if cached.is_file():
            return cached
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.client.fget_object(MINIO_BUCKET, key, str(cached))
        except Exception as e:  # S3Error: объект удалён/недоступен — как «файл утрачен»
            log.warning("MinIO: не удалось скачать %s: %s", key, e)
            return None
        return cached

    def _invalidate_cache(self, key: str) -> None:
        suffix = Path(key).suffix
        cached = self._cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()}{suffix}"
        cached.unlink(missing_ok=True)

    def delete(self, uri: str) -> None:
        key = self._key(uri)
        try:
            # Удаляем ВСЕ версии: обычный remove в версионируемом бакете лишь
            # ставит delete-marker, и объект продолжает занимать место.
            for obj in self.client.list_objects(
                MINIO_BUCKET, prefix=key, include_version=True,
            ):
                self.client.remove_object(
                    MINIO_BUCKET, key, version_id=obj.version_id,
                )
        except Exception as e:
            log.warning("MinIO: не удалось удалить %s: %s", key, e)
        self._invalidate_cache(key)

    def replace(self, uri: str, data: bytes) -> None:
        key = self._key(uri)
        self._put(key, data)  # versioning бакета сохраняет прежнюю версию сам
        self._invalidate_cache(key)

    def versions(self, uri: str) -> list[dict]:
        key = self._key(uri)
        items: list[dict] = []
        try:
            for obj in self.client.list_objects(
                MINIO_BUCKET, prefix=key, include_version=True,
            ):
                if obj.object_name != key or obj.is_delete_marker:
                    continue
                items.append({
                    "version_id": obj.version_id,
                    "is_current": bool(obj.is_latest),
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat()
                    if obj.last_modified else None,
                })
        except Exception as e:
            log.warning("MinIO: не удалось получить версии %s: %s", key, e)
        items.sort(key=lambda v: (not v["is_current"], v["last_modified"] or ""),
                   )
        return items


_local = LocalStorage()
_minio: MinioStorage | None = None


def default_storage() -> LocalStorage | MinioStorage:
    """Бэкенд для НОВЫХ загрузок — по конфигу STORAGE_BACKEND."""
    global _minio
    if STORAGE_BACKEND == "minio":
        if _minio is None:
            _minio = MinioStorage()
        return _minio
    return _local


def storage_for(uri: str) -> LocalStorage | MinioStorage:
    """Бэкенд для СУЩЕСТВУЮЩЕГО uri: в одной БД могут жить и локальные пути
    (загружены до включения MinIO), и minio:// — диспетчеризация по схеме."""
    global _minio
    if uri.startswith(_MINIO_SCHEME):
        if _minio is None:
            _minio = MinioStorage()
        return _minio
    return _local


def is_stored_file(uri: str | None) -> bool:
    """False для служебных записей (manual://) и пустых путей."""
    return bool(uri) and not uri.startswith(_MANUAL_SCHEME)


def open_local(uri: str | None) -> Path | None:
    """Локальный Path для чтения (пайплайн, отдача оригинала) или None."""
    if not is_stored_file(uri):
        return None
    return storage_for(uri).open_local(uri)


def delete_quietly(uri: str | None) -> None:
    """Удаление файла-исходника; отсутствие файла/служебный uri — не ошибка."""
    if not is_stored_file(uri):
        return
    storage_for(uri).delete(uri)
