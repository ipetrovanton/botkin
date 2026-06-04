"""VLM-клиент должен иметь конечный timeout, чтобы зависший вызов не висел 600с.

Реальный инцидент: extract одной страницы шёл 207с (генерация дублей) при дефолтном
timeout 600с. Конечный timeout прерывает деградировавший вызов → страница пропускается,
документ сохраняет остальное (см. толерантность добора).
"""
from botkin.config import VLM_REQUEST_TIMEOUT
from botkin.llm.client import get_raw_client


def test_request_timeout_is_finite_and_from_config():
    assert VLM_REQUEST_TIMEOUT <= 180  # не дефолтные 600с
    client = get_raw_client()
    assert client.timeout == VLM_REQUEST_TIMEOUT
