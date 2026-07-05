"""Медицинский research-RAG: свежие публикации PubMed → чанки в общий векторный индекс.

Идея: локальная модель опирается не только на «вшитые» при обучении знания, но и на
актуальные исследования. Мы тянем абстракты из PubMed по набору тем (config rag.research),
складываем как отдельный источник `source="research"` в тот же sqlite-vec индекс, что и
справочники. Поэтому retriever.search подхватывает их автоматически, без изменений в поиске.

PubMed E-utilities: esearch (свежие PMID по теме) → efetch (XML с абстрактами).
Обновление идемпотентно по PMID (ON CONFLICT в store.upsert_chunks) и запускается
скриптом scripts/update_medical_research.py (ручной прогон или планировщик ОС).

См. https://www.ncbi.nlm.nih.gov/books/NBK25501/ (E-utilities, по состоянию на 2026-07).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from xml.etree import ElementTree as ET

import httpx

from botkin.config import (
    RESEARCH_EMAIL, RESEARCH_PER_TOPIC, RESEARCH_TOOL, RESEARCH_TOPICS,
)
from botkin.rag import store
from botkin.rag.embeddings import embed_texts

log = logging.getLogger(__name__)

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TIMEOUT = 30.0
# NCBI без API-ключа допускает ~3 запроса/сек; держим паузу с запасом.
_PAUSE = 0.4


def _esearch(client: httpx.Client, query: str, retmax: int) -> list[str]:
    """PMID'ы по теме, свежие сверху (sort=date)."""
    r = client.get(
        f"{_EUTILS}/esearch.fcgi",
        params={"db": "pubmed", "term": query, "retmax": retmax,
                "retmode": "json", "sort": "date",
                "tool": RESEARCH_TOOL, "email": RESEARCH_EMAIL},
    )
    r.raise_for_status()
    return r.json().get("esearchresult", {}).get("idlist", [])


def _efetch(client: httpx.Client, pmids: list[str]) -> str:
    r = client.get(
        f"{_EUTILS}/efetch.fcgi",
        params={"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract",
                "retmode": "xml", "tool": RESEARCH_TOOL, "email": RESEARCH_EMAIL},
    )
    r.raise_for_status()
    return r.text


def _parse_articles(xml_text: str) -> list[dict]:
    """XML PubMed → записи {pmid, title, abstract, journal, year}."""
    root = ET.fromstring(xml_text)
    out: list[dict] = []
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID") or ""
        title = "".join(art.find(".//ArticleTitle").itertext()) \
            if art.find(".//ArticleTitle") is not None else ""
        # Абстракт может состоять из нескольких секций (BACKGROUND/METHODS/...).
        sections = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label = ab.get("Label")
            text = "".join(ab.itertext()).strip()
            sections.append(f"{label}: {text}" if label else text)
        abstract = "\n".join(s for s in sections if s)
        journal = art.findtext(".//Journal/ISOAbbreviation") \
            or art.findtext(".//Journal/Title") or ""
        year = art.findtext(".//JournalIssue/PubDate/Year") \
            or art.findtext(".//JournalIssue/PubDate/MedlineDate") or ""
        if pmid and (title or abstract):
            out.append({"pmid": pmid, "title": title.strip(), "abstract": abstract,
                        "journal": journal.strip(), "year": year.strip()})
    return out


def _chunk(rec: dict, topic: str) -> dict:
    """Публикация → чанк. Русское обрамление + англоязычный абстракт (bge-m3 мультиязычный)."""
    url = f"https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/"
    header = (f"Медицинская публикация (PubMed, тема «{topic}»). "
              f"Заголовок: {rec['title']}. Журнал: {rec['journal']}, {rec['year']}.")
    body = f" Резюме: {rec['abstract']}" if rec["abstract"] else ""
    return {
        "ref_key": f"pmid:{rec['pmid']}",
        "text": (header + body)[:4000],
        "meta": {**rec, "url": url, "topic": topic},
    }


def fetch_topic(client: httpx.Client, topic: str, per_topic: int) -> list[dict]:
    pmids = _esearch(client, topic, per_topic)
    if not pmids:
        return []
    time.sleep(_PAUSE)
    articles = _parse_articles(_efetch(client, pmids))
    return [_chunk(a, topic) for a in articles]


def index_research(topics: list[str] | None = None, per_topic: int | None = None) -> dict:
    """(Пере)индексация свежих публикаций PubMed по темам. Идемпотентно по PMID.

    Возвращает {"indexed": N, "topics": {...}, "updated_at": iso}."""
    topics = topics or RESEARCH_TOPICS
    per_topic = per_topic or RESEARCH_PER_TOPIC
    all_chunks: dict[str, dict] = {}  # ref_key → chunk (дедуп между темами)
    per_topic_counts: dict[str, int] = {}
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": RESEARCH_TOOL}) as client:
        for topic in topics:
            try:
                chunks = fetch_topic(client, topic, per_topic)
            except (httpx.HTTPError, ET.ParseError) as e:
                log.error("PubMed тема «%s» не загрузилась: %s", topic, e)
                per_topic_counts[topic] = 0
                continue
            per_topic_counts[topic] = len(chunks)
            for c in chunks:
                all_chunks.setdefault(c["ref_key"], c)
            log.info("PubMed «%s»: %d публикаций", topic, len(chunks))
            time.sleep(_PAUSE)

    items = list(all_chunks.values())
    if items:
        with store.vec_conn() as conn:
            embeddings = embed_texts([c["text"] for c in items])
            store.upsert_chunks(conn, "research", items, embeddings)
    result = {"indexed": len(items), "topics": per_topic_counts,
              "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    _write_manifest(result)
    return result


def _write_manifest(result: dict) -> None:
    """Снимок последнего обновления — для автономного режима и отладки."""
    from pathlib import Path

    from botkin.config import SQLITE_PATH
    path = Path(SQLITE_PATH).parent / "research_manifest.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
