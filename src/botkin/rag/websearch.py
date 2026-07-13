"""Живой веб-доступ для рекомендаций: веб-поиск (DuckDuckGo) + свежий PubMed.

Модель не вызывает инструменты сама (uncensored-модели плохо держат tool-calling) —
вместо этого мы делаем поиск за неё и подмешиваем результаты в контекст (augmentation).
Это надёжнее и работает одинаково на всех моделях.

DuckDuckGo html-endpoint не требует ключа. Ссылки в выдаче — редиректы вида
//duckduckgo.com/l/?uddg=<url-encoded>, раскодируем в исходный URL.
"""
from __future__ import annotations

import html
import logging
import re
import urllib.parse

import httpx

from botkin.config import RESEARCH_EMAIL, RESEARCH_TOOL

log = logging.getLogger(__name__)

# Lite-endpoint отдаёт прямые URL и заголовки без JS и без uddg-редиректов; он
# стабильнее html-версии, которая часто отвечает 202 (антибот) при частых запросах.
_DDG_LITE = "https://lite.duckduckgo.com/lite/"
_PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TIMEOUT = 20.0
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_ANCHOR_RE = re.compile(r'<a[^>]+href="(?P<href>https?://[^"]+)"[^>]*>(?P<title>.*?)</a>',
                        re.DOTALL)
_SNIPPET_RE = re.compile(r'result-snippet[^>]*>(?P<snippet>.*?)</td>', re.DOTALL)
_TAG_RE = re.compile(r"<.*?>")


def _clean(raw: str) -> str:
    return html.unescape(_TAG_RE.sub("", raw)).strip()


def web_search(query: str, max_results: int = 4) -> list[dict]:
    """Топ веб-результатов: [{title, url, snippet}]. При сбое — пустой список."""
    try:
        r = httpx.post(_DDG_LITE, data={"q": query}, headers={"User-Agent": _UA},
                       timeout=_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Веб-поиск не удался (%s): %s", query, e)
        return []
    snippets = [_clean(m.group("snippet")) for m in _SNIPPET_RE.finditer(r.text)]
    out: list[dict] = []
    seen: set[str] = set()
    for m in _ANCHOR_RE.finditer(r.text):
        url = m.group("href")
        # отсекаем внутренние ссылки DuckDuckGo (навигация, реклама)
        if "duckduckgo.com" in urllib.parse.urlparse(url).netloc or url in seen:
            continue
        seen.add(url)
        idx = len(out)
        out.append({
            "title": _clean(m.group("title")),
            "url": url,
            "snippet": snippets[idx] if idx < len(snippets) else "",
        })
        if len(out) >= max_results:
            break
    return out


def pubmed_search(query: str, max_results: int = 3) -> list[dict]:
    """Живой поиск свежих абстрактов PubMed (без индексации): [{title, url, snippet}]."""
    try:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
            s = c.get(f"{_PUBMED}/esearch.fcgi", params={
                "db": "pubmed", "term": query, "retmax": max_results,
                "retmode": "json", "sort": "date",
                "tool": RESEARCH_TOOL, "email": RESEARCH_EMAIL})
            s.raise_for_status()
            ids = s.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return []
            f = c.get(f"{_PUBMED}/efetch.fcgi", params={
                "db": "pubmed", "id": ",".join(ids), "rettype": "abstract",
                "retmode": "xml", "tool": RESEARCH_TOOL, "email": RESEARCH_EMAIL})
            f.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("PubMed live не удался (%s): %s", query, e)
        return []
    from botkin.rag.research import _parse_articles
    out = []
    for a in _parse_articles(f.text):
        out.append({
            "title": a["title"],
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{a['pmid']}/",
            "snippet": (a["abstract"] or "")[:600],
        })
    return out


def gather_context(query: str, max_web: int = 4) -> str:
    """Единый блок «свежих источников из интернета» для промпта. Пусто → ''."""
    web = web_search(query, max_web)
    pubmed = pubmed_search(query, 3)
    if not web and not pubmed:
        return ""
    lines: list[str] = []
    for r in web:
        lines.append(f"- [веб] {r['title']}: {r['snippet']} ({r['url']})")
    for r in pubmed:
        lines.append(f"- [PubMed] {r['title']}: {r['snippet']} ({r['url']})")
    return "\n".join(lines)
