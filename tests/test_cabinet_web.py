"""Тесты клиентской логики веб-кабинета (app.js) через node.

app.js — браузерный скрипт без модульной системы, поэтому исполняем его в node
с заглушкой localStorage и проверяем чистые функции: экранирование (XSS),
стадии прогресса, форматтеры. DOM-зависимые части (renderChart) проверяются
статически: все внешние (OCR/LLM) данные в innerHTML-шаблоне обязаны идти
через escapeHtml.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "src" / "botkin" / "web" / "app.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")


def run_js(snippet: str) -> str:
    """Исполняет app.js + snippet в node, возвращает stdout."""
    bootstrap = (
        "globalThis.localStorage = { getItem: () => null, setItem: () => {} };\n"
        + APP_JS.read_text(encoding="utf-8")
        + "\n" + snippet
    )
    res = subprocess.run(
        ["node", "-e", bootstrap], capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def test_escape_html_neutralizes_markup():
    """Крафтовое имя показателя из OCR не должно ломать разметку графика."""
    out = run_js(
        'console.log(escapeHtml(`"><svg onload=alert(1)>&<i>`));'
    )
    assert out == "&quot;&gt;&lt;svg onload=alert(1)&gt;&amp;&lt;i&gt;"


def test_escape_html_handles_null_and_numbers():
    out = run_js("console.log(JSON.stringify([escapeHtml(null), escapeHtml(42)]));")
    assert json.loads(out) == ["", "42"]


def test_stage_done_knows_processing_status():
    """processing — статус между received и recognizing: «Принят» уже done."""
    out = run_js(
        "const c = cabinet();"
        "console.log(JSON.stringify(["
        "c.stageDone({status:'processing'}, 'received'),"   # принят — завершён
        "c.stageDone({status:'processing'}, 'recognizing'),"  # распознавание — ещё нет
        "c.stageDone({status:'normalizing'}, 'recognizing'),"  # распознавание — завершено
        "c.stageDone({status:'bogus'}, 'received'),"  # неизвестный статус — не done
        "]));"
    )
    assert json.loads(out) == [True, False, True, False]


def test_progress_pct_covers_every_poll_status():
    """Каждый статус пайплайна имеет ненулевой прогресс — полоса не обнуляется."""
    out = run_js(
        "const c = cabinet();"
        "const sts = ['received','processing','recognizing','normalizing','extracted','failed'];"
        "console.log(JSON.stringify(sts.map(s => c.progressPct({status: s}))));"
    )
    assert all(p > 0 for p in json.loads(out))


def test_render_chart_escapes_all_external_interpolations():
    """В innerHTML-шаблоне графика все внешние данные экранированы.

    dynamics.analyte / dynamics.unit / taken_at приходят из OCR-извлечения
    загруженного документа — вставка без escapeHtml открывает stored XSS.
    """
    src = APP_JS.read_text(encoding="utf-8")
    for needle in [r"this\.dynamics\.analyte", r"this\.dynamics\.unit",
                   r"fmtDateShort\(p\.taken_at\)"]:
        # Каждое вхождение внутри шаблонов renderChart — только под escapeHtml(...).
        bare = re.findall(rf"(?<!escapeHtml\(){needle}(?!\s*[=;,)])", src)
        wrapped = re.findall(rf"escapeHtml\({needle}", src)
        assert wrapped, f"нет экранированных вставок {needle}"
        # Неэкранированные допустимы только вне шаблонных строк (присваивания и т.п.).
        for m in bare:
            assert "${" + m not in src, f"неэкранированная вставка в шаблоне: {m}"
