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
import tempfile
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "src" / "botkin" / "web" / "app.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")


def run_js(snippet: str) -> str:
    """Исполняет app.js + snippet в node, возвращает stdout.

    Скрипт пишется во временный файл: `node -e` со всем app.js в аргументе
    упирается в лимит длины командной строки Windows (32767 симв., WinError 206).
    """
    bootstrap = (
        "globalThis.localStorage = { getItem: () => null, setItem: () => {} };\n"
        + APP_JS.read_text(encoding="utf-8")
        + "\n" + snippet
    )
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "app_test.js"
        script.write_text(bootstrap, encoding="utf-8")
        # encoding обязателен: text=True на Windows декодирует stdout в cp1251,
        # а node пишет UTF-8 — кириллица в выводе превращалась бы в мусор.
        res = subprocess.run(
            ["node", str(script)], capture_output=True, text=True,
            encoding="utf-8", timeout=30,
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


def test_upload_error_text_names_the_actual_reason():
    """Отказ загрузки объясняется причиной, а не общим «не удалось».

    Бэкенд различает 413 (больше лимита), 400 (пустой файл) и 415
    (неподдерживаемый формат) — см. src/botkin/api/routes/upload.py.
    Раньше фронт показывал на любой не-ok один текст, и пользователь не мог
    понять, повторять загрузку или брать другой файл. Особенно заметно с
    accept="image/*": диалог предлагает GIF/BMP, а сервер их отвергает.
    """
    out = run_js(
        "console.log(JSON.stringify(["
        "uploadErrorText('скан.gif', 415),"
        "uploadErrorText('скан.pdf', 413),"
        "uploadErrorText('скан.pdf', 400),"
        "uploadErrorText('скан.pdf', 500),"   # неизвестный код — не выдумываем причину
        "uploadErrorText('скан.pdf', null),"  # сетевой сбой, кода нет
        "]));"
    )
    fmt, too_big, empty, server, offline = json.loads(out)
    assert "скан.gif" in fmt and "формат" in fmt
    # Лимит в тексте берётся из той же константы, что и клиентская проверка.
    assert "20 МБ" in too_big
    assert "пуст" in empty
    assert server == offline == "Не удалось загрузить скан.pdf"


def test_upload_error_text_lists_only_supported_formats():
    """Перечень в сообщении совпадает с upload.allowed_extensions бэкенда."""
    out = run_js("console.log(uploadErrorText('f', 415));")
    for fmt in ["PDF", "JPG", "PNG", "HEIC", "WebP"]:
        assert fmt in out, f"формат {fmt} не назван пользователю: {out}"
    # GIF/BMP сервер не принимает — обещать их нельзя.
    assert "GIF" not in out and "BMP" not in out


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


def test_ref_position_maps_value_into_corridor():
    """refPosition: положение значения на мини-шкале нормы (проценты, клэмп).

    Коридор занимает середину шкалы (25-75%): значение в норме попадает внутрь,
    выходы за референс видны слева/справа, экстремумы прижимаются к краям.
    """
    out = run_js(
        "const c = cabinet();"
        "console.log(JSON.stringify(["
        "c.refPosition({value_num: 130, ref_low: 120, ref_high: 160}),"
        "c.refPosition({value_num: 120, ref_low: 120, ref_high: 160}),"
        "c.refPosition({value_num: 160, ref_low: 120, ref_high: 160}),"
        "c.refPosition({value_num: 200, ref_low: 120, ref_high: 160}),"
        "c.refPosition({value_num: 0,   ref_low: 120, ref_high: 160}),"
        "c.refPosition({value_num: null, ref_low: 120, ref_high: 160}),"
        "c.refPosition({value_num: 130, ref_low: null, ref_high: 160}),"
        "c.refPosition({value_num: 130, ref_low: 130, ref_high: 130}),"
        "]));"
    )
    pos = json.loads(out)
    assert pos[1] == 25 and pos[2] == 75          # границы коридора
    assert 25 < pos[0] < 75                       # в норме — внутри
    assert pos[3] == 100 and pos[4] == 0          # клэмп к краям
    assert pos[5] is None and pos[6] is None and pos[7] is None


def test_selection_toggle_and_select_all():
    """Режим выбора: галочки по одному, «выбрать все» и полное снятие."""
    out = run_js(
        "const c = cabinet();"
        "c.docs = { items: [{id:1},{id:2},{id:3}], total: 3 };"
        "c.toggleSel(2); c.toggleSel(3); const step1 = [...c.selected];"
        "c.toggleSel(3); const step2 = [...c.selected];"
        "c.selectAllDocs(); const all = [...c.selected];"
        "const is2 = c.isSel(2);"  # в момент, когда выбраны все
        "c.selectAllDocs(); const none = [...c.selected];"  # повторный клик = снять все
        "console.log(JSON.stringify([step1, step2, all, none, is2]));"
    )
    step1, step2, all_, none_, is2 = json.loads(out)
    assert step1 == [2, 3]
    assert step2 == [2]
    assert sorted(all_) == [1, 2, 3]
    assert none_ == []
    assert is2 is True


def test_sel_mode_exit_clears_selection():
    """Выход из режима выбора сбрасывает выбранное — нет «невидимых» отметок."""
    out = run_js(
        "const c = cabinet();"
        "c.docs = { items: [{id:1},{id:2}], total: 2 };"
        "c.selMode = true; c.toggleSel(1);"
        "c.toggleSelMode();"
        "console.log(JSON.stringify([c.selMode, c.selected]));"
    )
    assert json.loads(out) == [False, []]
