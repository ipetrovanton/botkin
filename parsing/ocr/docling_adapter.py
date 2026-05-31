"""Docling для PDF с text-layer и таблицами (TableFormer)."""
from pathlib import Path
from docling.document_converter import DocumentConverter
from parsing.contracts import OCRResult


def docling_parse(pdf_path: Path) -> OCRResult:
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    # Markdown — основной текст с заголовками
    md_text = doc.export_to_markdown()

    # Таблицы — отдельный список для будущего extract
    tables_md = []
    for table in doc.tables:
        tables_md.append(table.export_to_markdown(doc=doc))

    # Confidence у Docling нет явной метрики; используем эвристику:
    # длина извлечённого текста / длина текстового слоя
    confidence = 0.85 if len(md_text) > 200 else 0.5

    return OCRResult(
        text=md_text,
        tables_markdown=tables_md,
        confidence=confidence,
        engine="docling",
    )
