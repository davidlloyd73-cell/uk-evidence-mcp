#!/usr/bin/env python3
"""
uk-evidence-mcp
================
An MCP server giving an AI agent access to UK-accessible clinical evidence:
NICE guidance, NICE Clinical Knowledge Summaries (CKS), and PubMed.

Built as a UK-usable alternative to OpenEvidence (which withdrew from the
UK/EU in 2026). All sources are public; no API key required.

Tools
-----
  nice_search(query, max_results)        Search NICE guidance / quality standards / TAs.
  nice_guidance(reference, chapter)      Full text of a NICE guidance chapter (e.g. NG196).
  cks_search(query, max_results)         Find CKS topics by name (the GP point-of-care summaries).
  cks_topic(topic)                       Summary + section map for a CKS topic.
  cks_section(topic, section)            Full text of one CKS section (e.g. management).
  mhra_search(query, max_results, kind)  Search MHRA Drug Safety Updates and safety alerts.
  mhra_article(path)                     Full text of one MHRA DSU / safety alert.
  pubmed_search(query, max_results)      Search PubMed; returns titles, journals, PMIDs.
  pubmed_abstract(pmid)                  Full abstract for one or more PMIDs.

Run:  python server.py   (speaks MCP over stdio)
"""

import re
import json
import html
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# NCBI asks that you identify yourself; this is courtesy, not authentication.
CONTACT_EMAIL = "davidlloyd73@gmail.com"
TOOL_NAME = "uk-evidence-mcp"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
NICE_BASE = "https://www.nice.org.uk"
CKS_BASE = "https://cks.nice.org.uk"
GOVUK = "https://www.gov.uk"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

TIMEOUT = httpx.Timeout(25.0)
MAX_TEXT_CHARS = 45_000  # guard against returning an entire 200-page guideline

mcp = FastMCP(TOOL_NAME)

# A small in-process cache so we only fetch the 393-topic CKS index once.
_cks_index_cache: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

async def _get(url: str, *, browser: bool = True) -> httpx.Response:
    headers = {
        "User-Agent": BROWSER_UA if browser else f"{TOOL_NAME} ({CONTACT_EMAIL})",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        return await client.get(url, headers=headers)


def _strip(html: str) -> str:
    """Extract readable text from a NICE/CKS page, dropping chrome."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "button"]):
        tag.decompose()
    main = soup.find("main") or soup.find(id="content") or soup.body or soup
    text = main.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n\n[... truncated; request a specific chapter/section for the rest ...]"
    return text


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _as_text(value) -> str:
    """Coerce a JSON field to a clean string.

    Some NICE search fields (e.g. ``niceResultType``) come back as a string for
    most results but as a list when a document has several values. Passing a
    list into ``str.join`` raises
    'sequence item 0: expected str instance, list found', so flatten any
    list/tuple to a comma-joined string and normalise None to "".
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_text(v) for v in value if v not in (None, ""))
    return html.unescape(str(value))


# --------------------------------------------------------------------------- #
# NICE guidance
# --------------------------------------------------------------------------- #

@mcp.tool()
async def nice_search(query: str, max_results: int = 10) -> str:
    """Search NICE for guidance, quality standards and technology appraisals.

    Use this to find the authoritative UK guideline on a clinical topic
    (e.g. "type 2 diabetes management", "atrial fibrillation anticoagulation").
    Returns titles, reference codes (e.g. NG196), type, publication date, a
    short abstract and the URL. Follow up with nice_guidance() for full text.

    Args:
        query: Free-text clinical query.
        max_results: How many results to return (default 10, max 25).
    """
    max_results = max(1, min(max_results, 25))
    url = f"{NICE_BASE}/search?q={quote_plus(query)}"
    r = await _get(url)
    if r.status_code != 200:
        return f"NICE search failed (HTTP {r.status_code})."
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        return "Could not parse NICE search results (page structure changed)."
    try:
        docs = json.loads(m.group(1))["props"]["pageProps"]["results"]["documents"]
    except (KeyError, json.JSONDecodeError):
        return "Could not extract NICE search documents."
    if not docs:
        return f"No NICE results for '{query}'."

    out = [f"NICE results for '{query}' ({min(len(docs), max_results)} shown):\n"]
    for d in docs[:max_results]:
        title = re.sub(r"<[^>]+>", "", _as_text(d.get("title") or d.get("titleNoHtml")) or "(untitled)")
        ref = _as_text(d.get("guidanceRef"))
        gtype = _as_text(d.get("niceResultType") or d.get("niceGuidanceType"))
        date = _as_text(d.get("lastUpdated") or d.get("publicationDate"))[:10]
        path = _as_text(d.get("pathAndQuery") or d.get("url"))
        link = path if path.startswith("http") else f"{NICE_BASE}{path}"
        abstract = re.sub(r"<[^>]+>", "", _as_text(d.get("abstract") or d.get("metaDescription"))).strip()
        head = f"- {title}"
        if ref:
            head += f"  [{ref}]"
        meta = " | ".join(x for x in (gtype, date) if x)
        out.append(head)
        if meta:
            out.append(f"    {meta}")
        if abstract:
            out.append(f"    {abstract[:280]}")
        out.append(f"    {link}")
    out.append("\nUse nice_guidance(reference=...) for the full text of any item with a reference code.")
    return "\n".join(out)


@mcp.tool()
async def nice_guidance(reference: str, chapter: Optional[str] = None) -> str:
    """Fetch the full text of a NICE guidance chapter.

    Args:
        reference: NICE reference code, e.g. "NG196", "CG181", "QS93".
        chapter: Optional chapter name. If omitted, the Recommendations
                 chapter is returned (the clinically actionable part), plus a
                 list of all available chapters so you can request another.

    Example: nice_guidance("NG196") or nice_guidance("NG196", "Context").
    """
    ref = reference.strip().lower()
    if not re.fullmatch(r"[a-z]+\d+", ref):
        return "Reference should look like NG196, CG181 or QS93."

    # Discover the chapter list from the overview page.
    overview = await _get(f"{NICE_BASE}/guidance/{ref}")
    if overview.status_code != 200:
        return f"Could not load NICE guidance {reference.upper()} (HTTP {overview.status_code})."
    soup = BeautifulSoup(overview.text, "html.parser")
    chapters: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        m = re.match(rf"/guidance/{ref}/chapter/([^/?#]+)", a["href"])
        if m:
            label = a.get_text(strip=True) or m.group(1).replace("-", " ").title()
            slug = m.group(1)
            if slug not in {c[1] for c in chapters}:
                chapters.append((label, slug))

    if not chapters:
        # No chaptered structure (e.g. some advice docs); return the overview text.
        return f"{reference.upper()}\n\n" + _strip(overview.text)

    # Resolve the requested chapter.
    target = None
    if chapter:
        want = _slugify(chapter)
        for label, slug in chapters:
            if want in slug or want in _slugify(label):
                target = (label, slug)
                break
        if target is None:
            avail = ", ".join(label for label, _ in chapters)
            return f"Chapter '{chapter}' not found in {reference.upper()}. Available: {avail}"
    else:
        for label, slug in chapters:
            if "recommend" in slug.lower():
                target = (label, slug)
                break
        target = target or chapters[0]

    page = await _get(f"{NICE_BASE}/guidance/{ref}/chapter/{target[1]}")
    if page.status_code != 200:
        return f"Could not load chapter '{target[0]}' (HTTP {page.status_code})."
    body = _strip(page.text)
    chapter_menu = ", ".join(label for label, _ in chapters)
    header = (
        f"NICE {reference.upper()} — chapter: {target[0]}\n"
        f"URL: {NICE_BASE}/guidance/{ref}/chapter/{target[1]}\n"
        f"All chapters: {chapter_menu}\n"
        + "-" * 60 + "\n"
    )
    return header + body


# --------------------------------------------------------------------------- #
# NICE Clinical Knowledge Summaries (CKS) — the GP point-of-care layer
# --------------------------------------------------------------------------- #

async def _load_cks_index() -> dict[str, str]:
    """Return {slug: display name} for all CKS topics, fetched once and cached."""
    if _cks_index_cache:
        return _cks_index_cache
    r = await _get(f"{CKS_BASE}/topics/")
    if r.status_code != 200:
        return {}
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        m = re.match(r"^/topics/([^/]+)/?$", a["href"])
        if m and m.group(1):
            name = a.get_text(strip=True)
            if name:
                _cks_index_cache[m.group(1)] = name
    return _cks_index_cache


@mcp.tool()
async def cks_search(query: str, max_results: int = 10) -> str:
    """Find NICE CKS topics matching a query.

    CKS (Clinical Knowledge Summaries) are the concise, primary-care-focused
    summaries GPs use at the point of care. This matches your query against the
    full topic list and returns the closest topic names and slugs. Follow up
    with cks_topic() for the summary and section map.

    Args:
        query: Condition or symptom, e.g. "atrial fibrillation", "gout", "UTI".
        max_results: How many topics to return (default 10).
    """
    index = await _load_cks_index()
    if not index:
        return "Could not load the CKS topic index."
    terms = [t for t in re.split(r"\W+", query.lower()) if t]

    def score(name: str, slug: str) -> int:
        hay = f"{name.lower()} {slug.replace('-', ' ')}"
        s = sum(1 for t in terms if t in hay)
        if query.lower() in name.lower():
            s += 3
        if _slugify(query) == slug:
            s += 5
        return s

    ranked = sorted(index.items(), key=lambda kv: score(kv[1], kv[0]), reverse=True)
    hits = [(slug, name) for slug, name in ranked if score(name, slug) > 0][:max_results]
    if not hits:
        return f"No CKS topic matched '{query}'. Try a broader term, or use nice_search()."
    out = [f"CKS topics matching '{query}':\n"]
    for slug, name in hits:
        out.append(f"- {name}  (topic id: {slug})")
        out.append(f"    {CKS_BASE}/topics/{slug}/")
    out.append("\nUse cks_topic(topic=...) with the topic id or name for the summary and sections.")
    return "\n".join(out)


@mcp.tool()
async def cks_topic(topic: str) -> str:
    """Get a CKS topic's summary and its list of sections.

    Args:
        topic: A CKS topic id (slug) or name, e.g. "atrial-fibrillation"
               or "atrial fibrillation".

    Returns the topic summary plus the available sections (Diagnosis,
    Management, Prescribing information, etc.). Use cks_section() for full text.
    """
    slug = topic if re.fullmatch(r"[a-z0-9-]+", topic) else _slugify(topic)
    r = await _get(f"{CKS_BASE}/topics/{slug}/")
    if r.status_code == 404:
        # Try to recover via the index.
        index = await _load_cks_index()
        cand = [s for s in index if _slugify(topic) in s or s in _slugify(topic)]
        if cand:
            slug = cand[0]
            r = await _get(f"{CKS_BASE}/topics/{slug}/")
    if r.status_code != 200:
        return f"Could not load CKS topic '{topic}' (HTTP {r.status_code}). Try cks_search() first."

    soup = BeautifulSoup(r.text, "html.parser")
    # Section links under this topic.
    sections: list[str] = []
    for a in soup.find_all("a", href=True):
        m = re.match(rf"^/topics/{re.escape(slug)}/([a-z0-9-]+(?:/[a-z0-9-]+)?)/?$", a["href"])
        if m:
            sec = m.group(1)
            if sec not in sections:
                sections.append(sec)
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form", "button"]):
        t.decompose()
    main = soup.find("main") or soup.body
    summary = re.sub(r"\n{2,}", "\n", main.get_text("\n", strip=True)) if main else ""
    summary = summary[:6000]

    out = [
        f"CKS topic: {slug}",
        f"URL: {CKS_BASE}/topics/{slug}/",
        "-" * 60,
        summary,
        "",
        "Available sections (use cks_section(topic, section)):",
    ]
    out += [f"  - {s}" for s in sections] or ["  (none parsed)"]
    return "\n".join(out)


@mcp.tool()
async def cks_section(topic: str, section: str) -> str:
    """Get the full text of one section of a CKS topic.

    Args:
        topic: CKS topic id/slug, e.g. "atrial-fibrillation".
        section: Section path as listed by cks_topic(), e.g. "management",
                 "diagnosis/assessment", "prescribing-information/digoxin".
    """
    slug = topic if re.fullmatch(r"[a-z0-9-]+", topic) else _slugify(topic)
    sec = section.strip("/")
    url = f"{CKS_BASE}/topics/{slug}/{sec}/"
    r = await _get(url)
    if r.status_code != 200:
        return (f"Could not load section '{sec}' of '{slug}' (HTTP {r.status_code}). "
                f"Use cks_topic('{slug}') to see valid section paths.")
    text = _strip(r.text)

    # Some top-level sections (e.g. "management") are thin hub pages whose real
    # content lives in child leaf pages. If so, fetch and concatenate children.
    if len(text) < 800 and "/" not in sec:
        topic_page = await _get(f"{CKS_BASE}/topics/{slug}/")
        if topic_page.status_code == 200:
            soup = BeautifulSoup(topic_page.text, "html.parser")
            children: list[str] = []
            for a in soup.find_all("a", href=True):
                m = re.match(rf"^/topics/{re.escape(slug)}/{re.escape(sec)}/([a-z0-9-]+)/?$", a["href"])
                if m and m.group(1) not in children:
                    children.append(m.group(1))
            if children:
                parts = [f"CKS: {slug} / {sec}  (expanded {len(children)} sub-sections)",
                         f"URL: {url}", "-" * 60]
                for child in children:
                    cp = await _get(f"{CKS_BASE}/topics/{slug}/{sec}/{child}/")
                    if cp.status_code == 200:
                        parts.append(f"\n### {sec}/{child}\n" + _strip(cp.text))
                combined = "\n".join(parts)
                if len(combined) > MAX_TEXT_CHARS:
                    combined = combined[:MAX_TEXT_CHARS] + "\n\n[... truncated; fetch a single sub-section for the rest ...]"
                return combined

    return f"CKS: {slug} / {sec}\nURL: {url}\n" + "-" * 60 + "\n" + text


# --------------------------------------------------------------------------- #
# MHRA — Drug Safety Updates and safety alerts (gov.uk open content API)
# --------------------------------------------------------------------------- #

# gov.uk document types relevant to medicines safety.
_MHRA_DOCTYPES = {
    "dsu": "drug_safety_update",      # monthly MHRA Drug Safety Update bulletins
    "alert": "medical_safety_alert",  # drug/device recalls and safety alerts
}


@mcp.tool()
async def mhra_search(query: str, max_results: int = 10, kind: str = "all") -> str:
    """Search MHRA Drug Safety Updates and medicine/device safety alerts.

    These are the UK regulator's post-marketing safety communications — the
    "have I missed a warning on this drug?" source. Returns titles, dates,
    a summary and the path to pass to mhra_article() for full text.

    Args:
        query: Drug, device or topic, e.g. "valproate", "SGLT2 ketoacidosis",
               "montelukast".
        max_results: How many results (default 10, max 25).
        kind: "dsu" for Drug Safety Update bulletins only, "alert" for
              recalls/safety alerts only, or "all" (default) for both.
    """
    max_results = max(1, min(max_results, 25))
    params = [
        f"q={quote_plus(query)}",
        f"count={max_results}",
        "fields=title,link,public_timestamp,description,content_store_document_type",
        "order=-public_timestamp" if not query.strip() else "",
    ]
    if kind in _MHRA_DOCTYPES:
        params.append(f"filter_content_store_document_type={_MHRA_DOCTYPES[kind]}")
    else:
        params.append(f"filter_content_store_document_type={_MHRA_DOCTYPES['dsu']}")
        params.append(f"filter_content_store_document_type={_MHRA_DOCTYPES['alert']}")
    url = f"{GOVUK}/api/search.json?" + "&".join(p for p in params if p)
    r = await _get(url, browser=False)
    if r.status_code != 200:
        return f"MHRA search failed (HTTP {r.status_code})."
    results = r.json().get("results", [])
    if not results:
        return f"No MHRA safety communications for '{query}'."
    out = [f"MHRA safety communications for '{query}':\n"]
    for d in results[:max_results]:
        dtype = d.get("content_store_document_type", "")
        label = "DSU" if dtype == "drug_safety_update" else "Alert"
        date = (d.get("public_timestamp") or "")[:10]
        desc = re.sub(r"\s+", " ", (d.get("description") or "")).strip()
        out.append(f"- [{label}] {d.get('title', '').strip()}")
        out.append(f"    {date} | path: {d.get('link', '')}")
        if desc:
            out.append(f"    {desc[:260]}")
    out.append("\nUse mhra_article(path=...) with the path for the full text.")
    return "\n".join(out)


@mcp.tool()
async def mhra_article(path: str) -> str:
    """Fetch the full text of one MHRA Drug Safety Update or safety alert.

    Args:
        path: The path from mhra_search(), e.g.
              "/drug-safety-update/valproate-dispense-full-packs..." (a full
              gov.uk URL is also accepted).
    """
    p = path.strip()
    if p.startswith("http"):
        p = re.sub(r"^https?://[^/]+", "", p)
    if not p.startswith("/"):
        p = "/" + p
    r = await _get(f"{GOVUK}/api/content{p}", browser=False)
    if r.status_code != 200:
        return f"Could not load MHRA article '{path}' (HTTP {r.status_code})."
    try:
        d = r.json()
    except json.JSONDecodeError:
        return "MHRA content API returned an unexpected response."
    title = d.get("title", "")
    updated = (d.get("public_updated_at") or "")[:10]
    body = d.get("details", {}).get("body", "")
    if isinstance(body, list):  # some docs use a list of {content_type, content}
        body = next((b.get("content", "") for b in body
                     if b.get("content_type") == "text/html"), body[0].get("content", "") if body else "")
    text = BeautifulSoup(body, "html.parser").get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n\n[... truncated ...]"
    header = f"MHRA: {title}\nUpdated: {updated}\nURL: {GOVUK}{p}\n" + "-" * 60 + "\n"
    return header + (text or "No body text found.")


# --------------------------------------------------------------------------- #
# PubMed (NCBI E-utilities)
# --------------------------------------------------------------------------- #

@mcp.tool()
async def pubmed_search(query: str, max_results: int = 10) -> str:
    """Search PubMed and return matching articles (title, journal, year, PMID).

    Good for the evidence-grounded second opinion: systematic reviews, trials,
    Cochrane reviews. Follow up with pubmed_abstract() for the abstract text.
    You can use PubMed syntax, e.g. 'apixaban AND atrial fibrillation AND
    (systematic review[pt] OR meta-analysis[pt])'.

    Args:
        query: PubMed query string.
        max_results: How many to return (default 10, max 30).
    """
    max_results = max(1, min(max_results, 30))
    esearch = (
        f"{EUTILS}/esearch.fcgi?db=pubmed&retmode=json&sort=relevance"
        f"&retmax={max_results}&term={quote_plus(query)}"
        f"&tool={TOOL_NAME}&email={quote_plus(CONTACT_EMAIL)}"
    )
    r = await _get(esearch, browser=False)
    if r.status_code != 200:
        return f"PubMed search failed (HTTP {r.status_code})."
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return f"No PubMed results for '{query}'."

    esum = (
        f"{EUTILS}/esummary.fcgi?db=pubmed&retmode=json&id={','.join(ids)}"
        f"&tool={TOOL_NAME}&email={quote_plus(CONTACT_EMAIL)}"
    )
    s = await _get(esum, browser=False)
    res = s.json().get("result", {})
    out = [f"PubMed results for '{query}':\n"]
    for pmid in ids:
        d = res.get(pmid, {})
        if not d:
            continue
        title = d.get("title", "").rstrip(".")
        journal = d.get("source", "")
        year = (d.get("pubdate", "") or "").split(" ")[0]
        ptypes = ", ".join(d.get("pubtype", []))
        authors = d.get("authors", [])
        first = authors[0]["name"] if authors else ""
        etal = " et al." if len(authors) > 1 else ""
        out.append(f"- {title}")
        out.append(f"    {first}{etal} | {journal} {year} | PMID {pmid}"
                   + (f" | {ptypes}" if ptypes else ""))
        out.append(f"    https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
    out.append("\nUse pubmed_abstract(pmid=...) for the abstract of any result.")
    return "\n".join(out)


@mcp.tool()
async def pubmed_abstract(pmid: str) -> str:
    """Fetch the full abstract(s) for one or more PubMed IDs.

    Args:
        pmid: A single PMID or a comma-separated list, e.g. "38745021" or
              "38745021,40012345".
    """
    ids = ",".join(re.findall(r"\d+", pmid))
    if not ids:
        return "Provide one or more numeric PMIDs."
    efetch = (
        f"{EUTILS}/efetch.fcgi?db=pubmed&rettype=abstract&retmode=text&id={ids}"
        f"&tool={TOOL_NAME}&email={quote_plus(CONTACT_EMAIL)}"
    )
    r = await _get(efetch, browser=False)
    if r.status_code != 200:
        return f"Could not fetch abstract(s) (HTTP {r.status_code})."
    text = r.text.strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n\n[... truncated ...]"
    return text or "No abstract text returned."


# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    mcp.run()
