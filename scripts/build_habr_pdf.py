"""Сборка единного PDF из статей и фактуры Хабра с встроенными графиками.

Запуск:
    .venv\\Scripts\\python.exe scripts\\build_habr_pdf.py

Подход: кастомный block-renderer поверх fpdf2 (markdown->HTML->write_html
в fpdf2 нестабилен для таблиц и изображений). Шрифт Consolas (кириллица).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fpdf import FPDF
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HABR = ROOT / "habr"
OUT = HABR / "botkin-habr-summary.pdf"

FONT_DIR = Path(r"C:\Windows\Fonts")
FONTS = {
    "regular": FONT_DIR / "consola.ttf",
    "bold": FONT_DIR / "consolab.ttf",
    "italic": FONT_DIR / "consolai.ttf",
    "bolditalic": FONT_DIR / "consolaz.ttf",
}

# Статьи по порядку + журнал + аналитика прогона.
SECTIONS: list[tuple[str, Path, Path | None]] = [
    (
        "Боткин: локальный медицинский ассистент на одной видеокарте",
        HABR / "botkin-habr-article.md",
        HABR / "bench-uncensored",
    ),
    (
        "Методология замера производительности и точности нейросетей",
        HABR / "benchmark-methodology.md",
        HABR / "bench-uncensored",
    ),
    (
        "Аналитика прогона uncensored-моделей (таблицы метрик)",
        HABR / "bench-uncensored" / "analysis.md",
        HABR / "bench-uncensored",
    ),
]


class HabrPDF(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(left=18, top=18, right=18)
        self.add_font("Consolas", "", str(FONTS["regular"]))
        self.add_font("Consolas", "B", str(FONTS["bold"]))
        self.add_font("Consolas", "I", str(FONTS["italic"]))
        self.add_font("Consolas", "BI", str(FONTS["bolditalic"]))
        self._in_section = False

    def header(self) -> None:
        if not self._in_section:
            return
        self.set_font("Consolas", "I", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, "Botkin — технический дневник (Хабр)", align="R")
        self.ln(4)
        self.set_draw_color(220, 220, 220)
        self.line(18, self.get_y(), 192, self.get_y())
        self.ln(3)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Consolas", "", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"стр. {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def _img_fit(path: Path, max_w: float, max_h: float) -> tuple[float, float]:
    with Image.open(path) as im:
        iw, ih = im.size
    ratio = iw / ih
    w = max_w
    h = w / ratio
    if h > max_h:
        h = max_h
        w = h * ratio
    return w, h


def _ensure_space(pdf: HabrPDF, need: float) -> None:
    if pdf.get_y() + need > pdf.h - 20:
        pdf.add_page()


def render_image(pdf: HabrPDF, alt: str, src: str, base: Path | None) -> None:
    p = Path(src)
    if not p.is_absolute() and base is not None:
        p = base / src
    if not p.exists():
        render_paragraph(pdf, f"[изображение не найдено: {src}]")
        return
    max_w = 174.0
    max_h = 110.0
    w, h = _img_fit(p, max_w, max_h)
    _ensure_space(pdf, h + 6)
    x = 18 + (174 - w) / 2
    pdf.image(str(p), x=x, w=w, h=h)
    pdf.ln(h + 1)
    if alt:
        pdf.set_font("Consolas", "I", 8)
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(0, 4, alt, align="C")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def render_heading(pdf: HabrPDF, level: int, text: str) -> None:
    sizes = {1: 18, 2: 14, 3: 12, 4: 11}
    size = sizes.get(level, 10)
    if level <= 2:
        pdf.add_page()
    else:
        _ensure_space(pdf, size + 4)
        pdf.ln(2)
    pdf.set_font("Consolas", "B", size)
    if level == 1:
        pdf.set_text_color(20, 60, 120)
    elif level == 2:
        pdf.set_text_color(30, 70, 130)
    else:
        pdf.set_text_color(50, 50, 50)
    pdf.multi_cell(0, size * 0.5 + 2, text)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def render_paragraph(pdf: HabrPDF, text: str) -> None:
    pdf.set_font("Consolas", "", 9)
    pdf.multi_cell(0, 5, text, markdown=True)
    pdf.ln(1.5)


def render_blockquote(pdf: HabrPDF, text: str) -> None:
    pdf.set_fill_color(245, 245, 240)
    pdf.set_font("Consolas", "I", 9)
    pdf.set_text_color(80, 80, 80)
    x0 = pdf.get_x()
    pdf.set_x(x0 + 4)
    pdf.multi_cell(166, 5, text, markdown=True, fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def render_code(pdf: HabrPDF, code: str) -> None:
    pdf.set_fill_color(240, 240, 235)
    pdf.set_font("Consolas", "", 8)
    pdf.set_text_color(40, 40, 40)
    for line in code.splitlines() or [""]:
        _ensure_space(pdf, 4.5)
        pdf.set_x(20)
        pdf.multi_cell(168, 4.5, line if line else " ", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def render_list_item(pdf: HabrPDF, marker: str, text: str, depth: int) -> None:
    pdf.set_font("Consolas", "", 9)
    indent = 6 + depth * 6
    _ensure_space(pdf, 5)
    pdf.set_x(18 + indent)
    pdf.cell(8, 5, marker)
    pdf.multi_cell(174 - indent - 8, 5, text, markdown=True)
    pdf.ln(0.5)


def render_hr(pdf: HabrPDF) -> None:
    pdf.ln(2)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.ln(3)


_TABLE_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")


def render_table(pdf: HabrPDF, rows: list[list[str]]) -> None:
    if not rows:
        return
    cols = max(len(r) for r in rows)
    rows = [r + [""] * (cols - len(r)) for r in rows]
    page_w = 174
    col_w = page_w / cols
    # Заголовок
    pdf.set_font("Consolas", "B", 8)
    pdf.set_fill_color(225, 230, 240)
    _ensure_space(pdf, 6)
    for i, cell in enumerate(rows[0]):
        pdf.cell(col_w, 6, cell.strip(), border=1, fill=True)
    pdf.ln()
    # Тело
    pdf.set_font("Consolas", "", 8)
    for row in rows[1:]:
        line_h = 5
        # оценка высоты по самому длинному тексту
        max_lines = 1
        for cell in row:
            txt = cell.strip()
            approx = max(1, int(len(txt) * 1.8 / (col_w * 1.9)) + 1)
            max_lines = max(max_lines, approx)
        line_h = 4.5 * max_lines
        _ensure_space(pdf, line_h + 1)
        for i, cell in enumerate(row):
            x = pdf.get_x()
            y = pdf.get_y()
            pdf.multi_cell(col_w, 4.5, cell.strip(), border=1, markdown=True)
            pdf.set_xy(x + col_w, y)
        pdf.ln(line_h)
    pdf.ln(2)


def render_markdown(pdf: HabrPDF, md: str, base: Path | None) -> None:
    lines = md.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Пустая строка
        if not line.strip():
            pdf.ln(1)
            i += 1
            continue
        # Изображение (отдельная строка)
        m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if m:
            render_image(pdf, m.group(1), m.group(2), base)
            i += 1
            continue
        # Заголовки
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            render_heading(pdf, level, m.group(2).strip())
            i += 1
            continue
        # HR
        if re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", line):
            render_hr(pdf)
            i += 1
            continue
        # Блок кода
        if line.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # закрывающий ```
            render_code(pdf, "\n".join(code_lines))
            continue
        # Цитата
        if line.startswith("> "):
            quote_lines: list[str] = []
            while i < n and lines[i].startswith("> "):
                quote_lines.append(lines[i][2:])
                i += 1
            render_blockquote(pdf, "\n".join(quote_lines))
            continue
        # Таблица
        if _TABLE_RE.match(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            table_rows: list[list[str]] = []
            # заголовок
            table_rows.append([c.strip() for c in line.strip("|").split("|")])
            i += 1  # сепаратор
            i += 1
            while i < n and _TABLE_RE.match(lines[i]):
                table_rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            render_table(pdf, table_rows)
            continue
        # Список (маркированный)
        m = re.match(r"^(\s*)([-*])\s+(.*)$", line)
        if m:
            # Глубина считается для каждого пункта внутри цикла: первая итерация
            # разбирает эту же строку, поэтому отдельная переменная не нужна.
            while i < n:
                mm = re.match(r"^(\s*)([-*])\s+(.*)$", lines[i])
                if not mm:
                    break
                d = len(mm.group(1)) // 2
                render_list_item(pdf, "•", mm.group(3), d)
                i += 1
            continue
        # Нумерованный список
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            while i < n:
                mm = re.match(r"^(\s*)(\d+)\.\s+(.*)$", lines[i])
                if not mm:
                    break
                d = len(mm.group(1)) // 2
                render_list_item(pdf, f"{mm.group(2)}.", mm.group(3), d)
                i += 1
            continue
        # Обычный абзац (склеиваем последовательные не-пустые строки до пустой/разметки)
        para_lines = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            if (
                not nxt.strip()
                or nxt.startswith("#")
                or nxt.startswith("```")
                or nxt.startswith("> ")
                or nxt.startswith("- ")
                or nxt.startswith("* ")
                or re.match(r"^\s*\d+\.\s+", nxt)
                or _TABLE_RE.match(nxt)
                or re.match(r"^!\[", nxt)
                or re.match(r"^(-{3,}|\*{3,})\s*$", nxt)
            ):
                break
            para_lines.append(nxt)
            i += 1
        render_paragraph(pdf, " ".join(part.strip() for part in para_lines))


def _mc(pdf: HabrPDF, h: float, text: str, size: int, style: str = "", align: str = "C") -> None:
    pdf.set_font("Consolas", style, size)
    pdf.set_x(18)
    pdf.multi_cell(174, h, text, align=align)


def build_title_page(pdf: HabrPDF) -> None:
    pdf.add_page()
    pdf.set_y(60)
    pdf.set_text_color(20, 60, 120)
    _mc(pdf, 12, "Botkin", 26, "B")
    pdf.ln(4)
    pdf.set_text_color(60, 60, 60)
    _mc(pdf, 8, "Локальная медицинская система распознавания\nанализов и рекомендаций", 14)
    pdf.ln(10)
    pdf.set_draw_color(20, 60, 120)
    pdf.line(60, pdf.get_y(), 150, pdf.get_y())
    pdf.ln(8)
    pdf.set_text_color(40, 40, 40)
    _mc(pdf, 6, "Сводный технический дневник для Хабра", 11)
    pdf.ln(2)
    _mc(pdf, 6, "Статьи + фактура + графики прогона uncensored-LLM", 11)
    pdf.ln(20)
    pdf.set_text_color(100, 100, 100)
    items = [
        "1. Статья: OCR анализов → RAG → uncensored-LLM → живой веб",
        "2. Методология замера производительности и точности",
        "3. Аналитика прогона 6 моделей (таблицы + 6 графиков)",
    ]
    for it in items:
        _mc(pdf, 5, it, 9)
    pdf.ln(20)
    pdf.set_text_color(130, 130, 130)
    _mc(pdf, 5, "Стек: Python 3.12, uv, Ollama (WSL2), sqlite-vec, bge-m3,\nPubMed E-utilities, DuckDuckGo Lite, PIL", 8, "I")
    pdf.set_text_color(0, 0, 0)


def main() -> int:
    pdf = HabrPDF()
    pdf._in_section = False
    build_title_page(pdf)

    for title, path, base in SECTIONS:
        if not path.exists():
            print(f"[WARN] нет файла: {path}", file=sys.stderr)
            continue
        pdf._in_section = True
        # Секционный разделитель-заголовок
        pdf.add_page()
        pdf.set_y(40)
        pdf.set_font("Consolas", "B", 16)
        pdf.set_text_color(20, 60, 120)
        pdf.multi_cell(0, 9, title, align="C")
        pdf.ln(4)
        pdf.set_draw_color(20, 60, 120)
        pdf.line(50, pdf.get_y(), 150, pdf.get_y())
        pdf.ln(6)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Consolas", "I", 8)
        pdf.set_text_color(110, 110, 110)
        pdf.multi_cell(0, 5, f"источник: {path.relative_to(ROOT)}", align="C")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

        md = path.read_text(encoding="utf-8")
        # Убираем самый первый H1 статьи (он дублирует секционный заголовок)
        md = re.sub(r"^#\s+.+\n", "", md, count=1)
        render_markdown(pdf, md, base)

    pdf.set_title("Botkin — сводный технический дневник для Хабра")
    pdf.set_author("Botkin project")
    pdf.alias_nb_pages()
    pdf.output(str(OUT))
    print(f"OK -> {OUT}  ({OUT.stat().st_size / 1024:.1f} KB, {pdf.page_no()} стр.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
