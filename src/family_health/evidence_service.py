from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from .query_planner import (
    QueryPlan,
    QueryRejected,
    ncbi_summary_url,
    plan_record,
    plan_search,
    pubmed_summary_url,
)
from .retriever import RetrievalError
from .source_registry import SOURCE_REGISTRY, source_catalog, validate_url_for_source


class JsonTransport(Protocol):
    def get_json(self, source_id: str, url: str) -> dict[str, Any]: ...

    def get_xml(self, source_id: str, url: str) -> bytes: ...

    def get_text(self, source_id: str, url: str) -> tuple[str, str]: ...


class _OfficialPageExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "svg", "form", "nav", "footer"})
    _CONTENT = frozenset({"main", "article"})
    _HEADINGS = frozenset({"h1", "h2", "h3"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.content_depth = 0
        self.title_depth = 0
        self.heading_tag: str | None = None
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.headings: list[str] = []
        self.content_parts: list[str] = []
        self.fallback_parts: list[str] = []
        self.document_date = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        if tag in self._SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self._CONTENT:
            self.content_depth += 1
        if tag == "title":
            self.title_depth += 1
        if tag in self._HEADINGS:
            self.heading_tag = tag
            self.heading_parts = []
        if tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").casefold()
            if name in {"article:published_time", "article:modified_time", "date", "dcterms.date"}:
                self.document_date = attributes.get("content", "")[:80]
        if tag == "time" and attributes.get("datetime"):
            self.document_date = attributes["datetime"][:80]

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._SKIP:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == self.heading_tag:
            heading = re.sub(r"\s+", " ", " ".join(self.heading_parts)).strip()
            if heading and len(self.headings) < 24:
                self.headings.append(heading[:240])
            self.heading_tag = None
            self.heading_parts = []
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        if tag in self._CONTENT and self.content_depth:
            self.content_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self.title_depth:
            self.title_parts.append(text)
        if self.heading_tag:
            self.heading_parts.append(text)
        self.fallback_parts.append(text)
        if self.content_depth:
            self.content_parts.append(text)

    def result(self) -> dict[str, Any]:
        parts = self.content_parts or self.fallback_parts
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()[:14_000]
        h1 = next((item for item in self.headings if item), "")
        title = h1 or re.sub(r"\s+", " ", " ".join(self.title_parts)).strip()
        return {
            "title": title[:1_000],
            "headings": self.headings,
            "text": text,
            "document_date": self.document_date or "unknown",
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(node: ET.Element | None, limit: int = 6_000) -> str:
    if node is None:
        return ""
    collapsed = re.sub(r"\s+", " ", " ".join(node.itertext())).strip()
    return collapsed[:limit]


def _parse_xml(payload: bytes) -> ET.Element:
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RetrievalError("invalid_xml_response") from exc


def _base_result(plan: QueryPlan) -> dict[str, Any]:
    source = SOURCE_REGISTRY[plan.source_id]
    return {
        "success": True,
        "plan": plan.as_dict(),
        "source_id": source.source_id,
        "source_owner": source.source_owner,
        "evidence_role": source.evidence_role,
        "may_support_patient_specific_recommendation": source.may_support_patient_specific_recommendation,
        "patient_specific_runtime_available": source.patient_specific_runtime_available,
        "runtime_access": source.runtime_access,
        "complete_interaction_check": False,
        "retrieved_at": _now(),
        "records": [],
        "limitations": [source.limitations],
    }


def _pubmed_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(result, Mapping):
        return []
    uids = result.get("uids")
    if not isinstance(uids, list):
        return []
    records: list[dict[str, Any]] = []
    for uid in uids[:5]:
        record = result.get(str(uid))
        if not isinstance(record, Mapping):
            continue
        pmid = str(record.get("uid") or uid)
        if not pmid.isdigit():
            continue
        records.append({
            "source_owner": SOURCE_REGISTRY["pubmed"].source_owner,
            "canonical_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "source_kind": "bibliographic_record",
            "jurisdiction": "international literature",
            "publication_or_version_date": str(record.get("pubdate") or "unknown"),
            "retrieved_at": _now(),
            "section_or_record_id": f"PMID:{pmid}",
            "freshness": "unknown",
            "freshness_basis": "publication date only; clinical currency not established",
            "clinical_currency": "not_established_from_bibliographic_metadata",
            "trust": "official_metadata",
            "limitations": SOURCE_REGISTRY["pubmed"].limitations,
            "title": str(record.get("title") or ""),
            "author": str(record.get("sortfirstauthor") or ""),
            "journal": str(record.get("fulljournalname") or ""),
            "publication_types": list(record.get("pubtype") or []),
        })
    return records


def _pubmed_xml_record(payload: bytes, expected_pmid: str) -> dict[str, Any] | None:
    root = _parse_xml(payload)
    article = root.find(".//{*}PubmedArticle")
    if article is None:
        return None
    pmid = _text(article.find(".//{*}MedlineCitation/{*}PMID"), 16)
    if pmid != expected_pmid:
        raise RetrievalError("record_identifier_mismatch")

    abstract_parts: list[str] = []
    for item in article.findall(".//{*}Abstract/{*}AbstractText"):
        body = _text(item, 4_000)
        if not body:
            continue
        label = str(item.attrib.get("Label") or "").strip()
        abstract_parts.append(f"{label}: {body}" if label else body)
    abstract = " ".join(abstract_parts)[:8_000]

    pub_date = article.find(".//{*}JournalIssue/{*}PubDate")
    date_parts = []
    if pub_date is not None:
        for field in ("Year", "Month", "Day", "MedlineDate"):
            value = _text(pub_date.find(f"{{*}}{field}"), 32)
            if value:
                date_parts.append(value)

    authors: list[str] = []
    for author in article.findall(".//{*}AuthorList/{*}Author")[:5]:
        name = " ".join(filter(None, (
            _text(author.find("{*}LastName"), 80),
            _text(author.find("{*}Initials"), 24),
        )))
        if name:
            authors.append(name)

    doi = ""
    for article_id in article.findall(".//{*}ArticleIdList/{*}ArticleId"):
        if str(article_id.attrib.get("IdType") or "").casefold() == "doi":
            doi = _text(article_id, 200)
            break
    correction_flags = sorted({
        str(item.attrib.get("RefType") or "").strip()
        for item in article.findall(".//{*}CommentsCorrections")
        if str(item.attrib.get("RefType") or "").strip()
    })
    normalized_flags = {flag.casefold().replace("_", "").replace(" ", "") for flag in correction_flags}
    retraction_review_required = any(
        "retract" in flag or "expressionofconcern" in flag
        for flag in normalized_flags
    )

    return {
        "source_owner": SOURCE_REGISTRY["pubmed"].source_owner,
        "canonical_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "source_kind": "bibliographic_record_with_indexed_abstract",
        "jurisdiction": "international literature",
        "publication_or_version_date": " ".join(date_parts) or "unknown",
        "retrieved_at": _now(),
        "section_or_record_id": f"PMID:{pmid}",
        "freshness": "unknown",
        "freshness_basis": "publication date and indexed record only; clinical currency not established",
        "clinical_currency": "not_established_from_bibliographic_record",
        "trust": "official_indexed_record",
        "limitations": SOURCE_REGISTRY["pubmed"].limitations,
        "title": _text(article.find(".//{*}ArticleTitle"), 1_000),
        "abstract": abstract,
        "abstract_available": bool(abstract),
        "authors": authors,
        "journal": _text(article.find(".//{*}Journal/{*}Title"), 300),
        "publication_types": [
            _text(item, 120) for item in article.findall(".//{*}PublicationTypeList/{*}PublicationType")
            if _text(item, 120)
        ],
        "doi": doi,
        "correction_or_retraction_flags": correction_flags,
        "retraction_review_required": retraction_review_required,
        "clinical_claim_status": "blocked_retraction_review" if retraction_review_required else "discovery_only",
        "content_classification": "untrusted_official_data",
    }


def _pmc_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(result, Mapping):
        return []
    uids = result.get("uids")
    if not isinstance(uids, list):
        return []
    records: list[dict[str, Any]] = []
    for uid in uids[:5]:
        row = result.get(str(uid))
        if not isinstance(row, Mapping):
            continue
        pmcid = ""
        article_ids = row.get("articleids")
        if isinstance(article_ids, list):
            for article_id in article_ids:
                if not isinstance(article_id, Mapping):
                    continue
                id_type = str(article_id.get("idtype") or article_id.get("idtypevalue") or "").casefold()
                value = str(article_id.get("value") or article_id.get("id") or "").upper()
                if id_type in {"pmc", "pmcid"} and re.fullmatch(r"PMC[1-9][0-9]{0,9}", value):
                    pmcid = value
                    break
        if not pmcid and str(uid).isdigit():
            pmcid = f"PMC{uid}"
        if not re.fullmatch(r"PMC[1-9][0-9]{0,9}", pmcid):
            continue
        records.append({
            "source_owner": SOURCE_REGISTRY["pmc"].source_owner,
            "canonical_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
            "source_kind": "full_text_archive_record",
            "jurisdiction": "international literature",
            "publication_or_version_date": str(row.get("pubdate") or "unknown"),
            "retrieved_at": _now(),
            "section_or_record_id": f"PMC:{pmcid}",
            "freshness": "unknown",
            "freshness_basis": "publication date only; clinical currency and article quality are not established",
            "trust": "official_archive_metadata",
            "limitations": SOURCE_REGISTRY["pmc"].limitations,
            "title": str(row.get("title") or "")[:1_000],
            "author": str(row.get("sortfirstauthor") or "")[:300],
            "journal": str(row.get("fulljournalname") or "")[:300],
            "clinical_claim_status": "discovery_only",
        })
    return records


def _lactmed_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(result, Mapping):
        return []
    uids = result.get("uids")
    if not isinstance(uids, list):
        return []
    records: list[dict[str, Any]] = []
    for uid in uids[:5]:
        row = result.get(str(uid))
        if not isinstance(row, Mapping):
            continue
        accession = str(row.get("chapteraccessionid") or "").upper()
        if not re.fullmatch(r"NBK[1-9][0-9]{2,9}", accession):
            continue
        records.append({
            "source_owner": SOURCE_REGISTRY["lactmed"].source_owner,
            "canonical_url": f"https://www.ncbi.nlm.nih.gov/books/{accession}/",
            "source_kind": "specialist_reference_record",
            "jurisdiction": "international literature",
            "publication_or_version_date": str(row.get("pubdate") or "unknown"),
            "retrieved_at": _now(),
            "section_or_record_id": f"LactMed:{accession}",
            "freshness": "unknown",
            "freshness_basis": "NCBI Bookshelf metadata only; review the exact current LactMed chapter",
            "trust": "official_reference_metadata",
            "limitations": SOURCE_REGISTRY["lactmed"].limitations,
            "title": str(row.get("title") or "")[:1_000],
            "book": str(row.get("bookname") or "")[:500],
            "clinical_claim_status": "requires_exact_chapter_and_patient_context",
        })
    return records


_OPENFDA_SAFETY_FIELDS = (
    "boxed_warning",
    "warnings",
    "warnings_and_cautions",
    "contraindications",
    "do_not_use",
    "ask_doctor",
    "ask_doctor_or_pharmacist",
    "stop_use",
    "drug_interactions",
    "pregnancy",
    "pregnancy_or_breast_feeding",
    "nursing_mothers",
    "pediatric_use",
    "geriatric_use",
    "adverse_reactions",
    "keep_out_of_reach_of_children",
)


def _bounded_strings(value: Any, *, item_limit: int = 3, char_limit: int = 2_400) -> list[str]:
    values = value if isinstance(value, list) else [value]
    output: list[str] = []
    for item in values[:item_limit]:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            output.append(text[:char_limit])
    return output


def _openfda_records(payload: Mapping[str, Any], max_results: int) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    records: list[dict[str, Any]] = []
    for row in rows[:max_results]:
        if not isinstance(row, Mapping):
            continue
        openfda = row.get("openfda") if isinstance(row.get("openfda"), Mapping) else {}
        sections = []
        remaining = 12_000
        for field in _OPENFDA_SAFETY_FIELDS:
            values = _bounded_strings(row.get(field))
            if not values or remaining <= 0:
                continue
            text = " ".join(values)[:remaining]
            sections.append({"section_name": field, "text": text})
            remaining -= len(text)
        if not sections:
            continue
        record_id = re.sub(r"[^A-Za-z0-9._:-]", "", str(row.get("id") or "unknown"))[:120] or "unknown"
        effective = str(row.get("effective_time") or "unknown")[:32]
        records.append({
            "source_owner": SOURCE_REGISTRY["openfda"].source_owner,
            "canonical_url": SOURCE_REGISTRY["openfda"].canonical_url,
            "source_kind": "regulatory_label_fallback",
            "jurisdiction": "United States",
            "publication_or_version_date": effective,
            "retrieved_at": _now(),
            "section_or_record_id": f"openFDA:{record_id}",
            "freshness": "unknown",
            "freshness_basis": "submission metadata only; currentness and completeness are not established",
            "trust": "official_api_fallback",
            "limitations": SOURCE_REGISTRY["openfda"].limitations,
            "substance_names": _bounded_strings(openfda.get("substance_name"), item_limit=8, char_limit=200),
            "brand_names": _bounded_strings(openfda.get("brand_name"), item_limit=8, char_limit=200),
            "sections": sections,
            "dose_sections_included": False,
            "complete_interaction_check": False,
            "clinical_claim_status": "fallback_only",
            "content_classification": "untrusted_official_data",
        })
    return records


def _cdc_records(payload: Mapping[str, Any], max_results: int) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    eligible = [row for row in rows if isinstance(row, Mapping)]
    eligible.sort(
        key=lambda row: str(row.get("dateModified") or row.get("datePublished") or ""),
        reverse=True,
    )
    records: list[dict[str, Any]] = []
    for row in eligible[:max_results]:
        if not isinstance(row, Mapping):
            continue
        canonical_url = str(row.get("sourceUrl") or row.get("targetUrl") or SOURCE_REGISTRY["cdc"].canonical_url)
        parsed_url = urlsplit(canonical_url)
        canonical_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", ""))
        try:
            validate_url_for_source("cdc", canonical_url, allow_query=False)
        except Exception:
            canonical_url = SOURCE_REGISTRY["cdc"].canonical_url
        record_id = re.sub(r"[^A-Za-z0-9._:-]", "", str(row.get("id") or "unknown"))[:120] or "unknown"
        records.append({
            "source_owner": SOURCE_REGISTRY["cdc"].source_owner,
            "canonical_url": canonical_url,
            "source_kind": "public_health_guidance",
            "jurisdiction": "United States",
            "publication_or_version_date": str(row.get("dateModified") or row.get("datePublished") or "unknown")[:80],
            "retrieved_at": _now(),
            "section_or_record_id": f"CDC:{record_id}",
            "freshness": "unknown",
            "freshness_basis": "CDC catalog metadata; verify the date and exact linked page before a clinical claim",
            "trust": "official_public_health_metadata",
            "limitations": SOURCE_REGISTRY["cdc"].limitations,
            "title": str(row.get("name") or row.get("title") or "")[:1_000],
            "description": re.sub(r"\s+", " ", str(row.get("description") or "")).strip()[:2_400],
            "diagnostic_claim": False,
            "content_classification": "untrusted_official_data",
        })
    return records


def _pmc_xml_record(payload: bytes, expected_pmcid: str) -> dict[str, Any] | None:
    root = _parse_xml(payload)
    article = root if root.tag.rsplit("}", 1)[-1] == "article" else root.find(".//{*}article")
    if article is None:
        return None
    pmcid = ""
    for article_id in article.findall(".//{*}article-id"):
        if str(article_id.attrib.get("pub-id-type") or "").casefold() in {"pmc", "pmcid"}:
            pmcid = _text(article_id, 24).upper()
            break
    if pmcid != expected_pmcid:
        raise RetrievalError("record_identifier_mismatch")
    date_node = article.find(".//{*}pub-date[@pub-type='epub']")
    if date_node is None:
        date_node = article.find(".//{*}pub-date")
    date_parts = []
    if date_node is not None:
        for field in ("year", "month", "day"):
            value = _text(date_node.find(f"{{*}}{field}"), 16)
            if value:
                date_parts.append(value)
    title = _text(article.find(".//{*}article-title"), 1_000)
    article_type = str(article.attrib.get("article-type") or "").casefold()
    retraction_review_required = "retract" in article_type or "retracted" in title.casefold()
    return {
        "source_owner": SOURCE_REGISTRY["pmc"].source_owner,
        "canonical_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
        "source_kind": "archived_full_text_article",
        "jurisdiction": "international literature",
        "publication_or_version_date": "-".join(date_parts) or "unknown",
        "retrieved_at": _now(),
        "section_or_record_id": f"PMC:{pmcid}",
        "freshness": "unknown",
        "freshness_basis": "article publication date only; clinical currency is not established",
        "trust": "official_full_text_archive",
        "limitations": SOURCE_REGISTRY["pmc"].limitations,
        "title": title,
        "journal": _text(article.find(".//{*}journal-title"), 300),
        "abstract": _text(article.find(".//{*}abstract"), 8_000),
        "body": _text(article.find(".//{*}body"), 12_000),
        "retraction_review_required": retraction_review_required,
        "clinical_claim_status": "blocked_retraction_review" if retraction_review_required else "discovery_only",
        "content_classification": "untrusted_official_data",
        "embedded_instructions_executable": False,
    }


_DAILYMED_SECTIONS = {
    "34066-1": "boxed_warning",
    "34067-9": "indications_and_usage",
    "34070-3": "contraindications",
    "34071-1": "warnings",
    "42232-9": "precautions",
    "34073-7": "drug_interactions",
    "42228-7": "pregnancy",
    "34080-2": "nursing_mothers",
    "34081-0": "pediatric_use",
    "34082-8": "geriatric_use",
    "34084-4": "adverse_reactions",
}

_DAILYMED_OTC_DOCUMENT_CODE = "34390-5"
_DAILYMED_DIRECTIONS_CODE = "34068-7"
_DAILYMED_ALLOWED_OTC_MARKETING_CATEGORIES = frozenset({
    "C200263",  # OTC Monograph Drug
    "C132334",  # OTC Monograph Drug Product Manufactured Under Contract
    "C73584",   # ANDA
    "C73594",   # NDA
    "C73605",   # NDA authorized generic
    "C132333",  # Approved Drug Product Manufactured Under Contract
})


def _dailymed_marketing_categories(root: ET.Element) -> list[dict[str, str]]:
    categories: dict[str, dict[str, str]] = {}
    for node in root.findall(".//{*}approval/{*}code"):
        code = str(node.attrib.get("code") or "").strip().upper()
        if not code:
            continue
        categories[code] = {
            "code": code,
            "display_name": str(node.attrib.get("displayName") or "").strip(),
        }
    return [categories[code] for code in sorted(categories)]


def _dailymed_products(root: ET.Element) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for node in root.findall(".//{*}manufacturedProduct"):
        name = _text(node.find("./{*}name"), 300)
        form_node = node.find("./{*}formCode")
        product_code_node = node.find("./{*}code")
        form = str(form_node.attrib.get("displayName") or "").strip() if form_node is not None else ""
        product_code = str(product_code_node.attrib.get("code") or "").strip() if product_code_node is not None else ""
        if not (name or form or product_code):
            continue

        active_ingredients: list[dict[str, str]] = []
        for ingredient in node.findall("./{*}ingredient[@classCode='ACTIB']"):
            substance = ingredient.find("./{*}ingredientSubstance")
            quantity = ingredient.find("./{*}quantity")
            numerator = quantity.find("./{*}numerator") if quantity is not None else None
            denominator = quantity.find("./{*}denominator") if quantity is not None else None
            active_ingredients.append({
                "name": _text(substance.find("./{*}name"), 200) if substance is not None else "",
                "strength_numerator": str(numerator.attrib.get("value") or "") if numerator is not None else "",
                "strength_unit": str(numerator.attrib.get("unit") or "") if numerator is not None else "",
                "strength_denominator": str(denominator.attrib.get("value") or "") if denominator is not None else "",
                "strength_denominator_unit": str(denominator.attrib.get("unit") or "") if denominator is not None else "",
            })

        key = (name.casefold(), form.casefold(), product_code.casefold())
        if key in seen:
            continue
        seen.add(key)
        products.append({
            "name": name,
            "dosage_form": form,
            "product_code": product_code,
            "active_ingredients": active_ingredients,
        })
        if len(products) >= 12:
            break
    return products


def _dailymed_label_record(payload: bytes, expected_setid: str) -> dict[str, Any]:
    root = _parse_xml(payload)
    setid_node = root.find("./{*}setId")
    setid = str(setid_node.attrib.get("root") or "").casefold() if setid_node is not None else ""
    if setid != expected_setid.casefold():
        raise RetrievalError("record_identifier_mismatch")

    version_node = root.find("./{*}versionNumber")
    effective_node = root.find("./{*}effectiveTime")
    effective = str(effective_node.attrib.get("value") or "") if effective_node is not None else ""
    if re.fullmatch(r"[0-9]{8}", effective):
        effective = f"{effective[:4]}-{effective[4:6]}-{effective[6:]}"

    document_code_node = root.find("./{*}code")
    document_code = str(document_code_node.attrib.get("code") or "").strip() if document_code_node is not None else ""
    document_type = str(document_code_node.attrib.get("displayName") or "").strip() if document_code_node is not None else ""
    marketing_categories = _dailymed_marketing_categories(root)
    category_codes = {item["code"] for item in marketing_categories}
    nonprescription_label_confirmed = bool(
        document_code == _DAILYMED_OTC_DOCUMENT_CODE
        and category_codes
        and category_codes <= _DAILYMED_ALLOWED_OTC_MARKETING_CATEGORIES
    )

    selected: dict[str, dict[str, str]] = {}
    remaining = 14_000
    for section in root.findall(".//{*}section"):
        code_node = section.find("./{*}code")
        code = str(code_node.attrib.get("code") or "") if code_node is not None else ""
        name = _DAILYMED_SECTIONS.get(code)
        if name is None or name in selected or remaining <= 0:
            continue
        body = _text(section.find("./{*}text"), min(2_400, remaining))
        if not body:
            continue
        selected[name] = {
            "section_code": code,
            "section_name": name,
            "heading": _text(section.find("./{*}title"), 200),
            "text": body,
        }
        remaining -= len(body)

    directions: dict[str, str] | None = None
    if nonprescription_label_confirmed:
        for section in root.findall(".//{*}section"):
            code_node = section.find("./{*}code")
            code = str(code_node.attrib.get("code") or "") if code_node is not None else ""
            if code != _DAILYMED_DIRECTIONS_CODE:
                continue
            body = _text(section.find("./{*}text"), 3_500)
            if body:
                directions = {
                    "section_code": code,
                    "section_name": "label_directions",
                    "heading": _text(section.find("./{*}title"), 200),
                    "text": body,
                }
            break

    return {
        "source_owner": SOURCE_REGISTRY["dailymed"].source_owner,
        "canonical_url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}",
        "source_kind": "structured_product_label",
        "jurisdiction": "United States",
        "publication_or_version_date": effective or "unknown",
        "retrieved_at": _now(),
        "section_or_record_id": f"DailyMed SETID:{setid}",
        "freshness": "current",
        "freshness_basis": "current Structured Product Label API record at retrieval",
        "freshness_scope": "label_record_only",
        "trust": "official_label_record",
        "limitations": SOURCE_REGISTRY["dailymed"].limitations,
        "title": _text(root.find("./{*}title"), 1_000),
        "version": str(version_node.attrib.get("value") or "unknown") if version_node is not None else "unknown",
        "document_code": document_code,
        "document_type": document_type,
        "marketing_categories": marketing_categories,
        "products": _dailymed_products(root),
        "sections": [selected[name] for name in _DAILYMED_SECTIONS.values() if name in selected],
        "nonprescription_label_confirmed": nonprescription_label_confirmed,
        "label_directions": directions,
        "directions_source_scope": "exact_product_label" if directions else None,
        "directions_require_exact_product_match": True,
        "patient_specific_dose_calculated": False,
        "dose_sections_included": bool(directions),
        "complete_interaction_check": False,
        "content_classification": "untrusted_official_data",
    }


def _rxnorm_record(row: Mapping[str, Any]) -> dict[str, Any]:
    rxcui = str(row.get("rxcui") or "")
    return {
        "source_owner": SOURCE_REGISTRY["rxnorm"].source_owner,
        "canonical_url": f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json",
        "source_kind": "drug_terminology",
        "jurisdiction": "United States terminology",
        "publication_or_version_date": "current API record",
        "retrieved_at": _now(),
        "section_or_record_id": f"RxCUI:{rxcui}",
        "freshness": "current",
        "freshness_basis": "current RxNorm terminology API record at retrieval",
        "freshness_scope": "drug_identity_only",
        "trust": "official_terminology_record",
        "limitations": SOURCE_REGISTRY["rxnorm"].limitations,
        "rxcui": rxcui,
        "name": str(row.get("name") or ""),
        "term_type": str(row.get("tty") or ""),
        "score": str(row.get("score") or ""),
    }


class EvidenceService:
    def __init__(self, transport: JsonTransport):
        self.transport = transport

    def catalog(self, topics: list[str] | None = None) -> dict[str, Any]:
        rows = source_catalog(topics)
        return {
            "success": True,
            "sources": rows,
            "source_count": len(rows),
            "runtime_accessible_count": sum(bool(row["executable_now"]) for row in rows),
            "catalog_only_count": sum(not bool(row["executable_now"]) for row in rows),
            "patient_specific_runtime_source_count": sum(
                bool(row["patient_specific_runtime_available"]) for row in rows
            ),
            "medical_model_memory_is_source": False,
            "local_applicability_status": "requires_current_setting_confirmation",
        }

    def fetch_official_page(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise QueryRejected("request_must_be_object")
        if set(request) - {"source_id", "url", "record_id"}:
            raise QueryRejected("unsupported_or_private_field")
        source_id = request.get("source_id")
        url = request.get("url")
        record_id = request.get("record_id")
        if source_id not in {"who", "uspstf", "rch", "ema", "lactmed"}:
            raise QueryRejected("source_has_no_exact_page_route")
        if not isinstance(url, str) or not isinstance(record_id, str):
            raise QueryRejected("exact_page_fields_required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .:_/-]{0,159}", record_id):
            raise QueryRejected("invalid_public_record_id")
        validate_url_for_source(source_id, url, allow_query=False)
        raw_html, final_url = self.transport.get_text(source_id, url)
        validate_url_for_source(source_id, final_url, allow_query=False)
        extractor = _OfficialPageExtractor()
        try:
            extractor.feed(raw_html)
            extracted = extractor.result()
        except Exception as exc:
            raise RetrievalError("invalid_official_page") from exc
        if not extracted["title"] or len(extracted["text"]) < 40:
            raise RetrievalError("official_page_content_incomplete")
        source = SOURCE_REGISTRY[source_id]
        record = {
            "source_owner": source.source_owner,
            "canonical_url": final_url,
            "source_kind": source.source_kind,
            "jurisdiction": source.jurisdiction,
            "publication_or_version_date": extracted["document_date"],
            "retrieved_at": _now(),
            "section_or_record_id": record_id,
            "freshness": "unknown",
            "freshness_basis": "exact official page retrieved; clinical currency must be checked from its displayed date",
            "trust": "official_public_page",
            "limitations": source.limitations,
            "title": extracted["title"],
            "headings": extracted["headings"],
            "text": extracted["text"],
            "content_classification": "untrusted_official_data",
            "embedded_instructions_executable": False,
        }
        return {
            "success": True,
            "source_id": source_id,
            "source_owner": source.source_owner,
            "evidence_role": source.evidence_role,
            "may_support_patient_specific_recommendation": source.may_support_patient_specific_recommendation,
            "access_status": "retrieved",
            "complete_interaction_check": False,
            "records": [record],
            "limitations": [source.limitations, "Exact-page retrieval is not proof that this is the newest applicable guidance."],
        }

    def search(self, request: Mapping[str, Any]) -> dict[str, Any]:
        plan = plan_search(request)
        result = _base_result(plan)
        payload = self.transport.get_json(plan.source_id, plan.url)
        if plan.source_id == "ncbi_eutils":
            ids = payload.get("esearchresult", {}).get("idlist", [])
            if not isinstance(ids, list) or not ids:
                result["records"] = []
                result["access_status"] = "no_records"
                result["limitations"].append("No matching official bibliographic records were returned.")
                return result
            summary = self.transport.get_json("ncbi_eutils", pubmed_summary_url([str(item) for item in ids]))
            result["records"] = _pubmed_records(summary)
            result["access_status"] = "retrieved"
            result["limitations"].append(
                "PubMed results are bibliographic metadata; read the full current guideline or paper before a clinical claim."
            )
            result["may_support_patient_specific_recommendation"] = False
            return result
        if plan.source_id in {"pmc", "lactmed"}:
            ids = payload.get("esearchresult", {}).get("idlist", [])
            if not isinstance(ids, list) or not ids:
                result["records"] = []
                result["access_status"] = "no_records"
                result["limitations"].append("No matching official NCBI records were returned.")
                return result
            database = "pmc" if plan.source_id == "pmc" else "books"
            summary_url = ncbi_summary_url(plan.source_id, database, [str(item) for item in ids])
            summary = self.transport.get_json(plan.source_id, summary_url)
            if plan.source_id == "pmc":
                result["records"] = _pmc_records(summary)
                result["limitations"].append(
                    "PMC availability gives lawful full-text access, not proof of article quality, currency or patient applicability."
                )
            else:
                result["records"] = _lactmed_records(summary)
                result["limitations"].append(
                    "Use the exact current LactMed chapter with maternal dose, infant age/health and clinical context."
                )
            result["access_status"] = "retrieved" if result["records"] else "no_records"
            result["may_support_patient_specific_recommendation"] = False
            return result
        if plan.source_id == "dailymed":
            rows = payload.get("data", []) if isinstance(payload, Mapping) else []
            result["records"] = [self._dailymed_record(row) for row in rows[: plan.max_results] if isinstance(row, Mapping)]
            result["access_status"] = "retrieved" if result["records"] else "no_records"
            result["limitations"].append("Label lookup is not a complete drug-drug interaction check.")
            return result
        if plan.source_id == "rxnorm":
            candidates = payload.get("approximateGroup", {}).get("candidate", [])
            result["records"] = [
                _rxnorm_record(row)
                for row in candidates[: plan.max_results] if isinstance(row, Mapping) and str(row.get("rxcui") or "").isdigit()
            ]
            result["access_status"] = "retrieved" if result["records"] else "no_records"
            result["may_support_patient_specific_recommendation"] = False
            return result
        if plan.source_id == "openfda":
            result["records"] = _openfda_records(payload, plan.max_results)
            result["access_status"] = "retrieved" if result["records"] else "no_records"
            result["may_support_patient_specific_recommendation"] = False
            result["clinical_use"] = "fallback_only"
            result["limitations"].append(
                "openFDA is a bounded label fallback only; dosage is omitted and the result cannot drive treatment."
            )
            return result
        if plan.source_id == "cdc":
            result["records"] = _cdc_records(payload, plan.max_results)
            result["access_status"] = "retrieved" if result["records"] else "no_records"
            result["limitations"].append(
                "CDC catalog results support public-health guidance discovery, not diagnosis for an individual."
            )
            return result
        studies = payload.get("studies", []) if isinstance(payload, Mapping) else []
        result["records"] = [self._trial_record(row) for row in studies[: plan.max_results] if isinstance(row, Mapping)]
        result["access_status"] = "retrieved" if result["records"] else "no_records"
        result["may_support_patient_specific_recommendation"] = False
        result["limitations"].append("Trial registration is discovery only and is not proof of efficacy or suitability.")
        return result

    def fetch_record(self, request: Mapping[str, Any]) -> dict[str, Any]:
        plan = plan_record(request)
        result = _base_result(plan)
        if plan.record_type == "pubmed_pmid":
            payload = self.transport.get_xml(plan.source_id, plan.url)
            record = _pubmed_xml_record(payload, str(plan.identifier))
            result["records"] = [record] if record else []
            result["may_support_patient_specific_recommendation"] = False
            result["limitations"].append(
                "An indexed abstract is discovery evidence, not a complete guideline or patient-specific recommendation."
            )
            if record and record["retraction_review_required"]:
                result["clinical_use_blocked"] = True
                result["clinical_use_block_reason"] = "retraction_or_expression_of_concern_review_required"
        elif plan.record_type == "pmc_pmcid":
            payload = self.transport.get_xml(plan.source_id, plan.url)
            record = _pmc_xml_record(payload, str(plan.identifier))
            result["records"] = [record] if record else []
            result["may_support_patient_specific_recommendation"] = False
            result["limitations"].append(
                "Archived full text is discovery evidence; assess study design, retraction status, currency and applicability separately."
            )
            if record and record["retraction_review_required"]:
                result["clinical_use_blocked"] = True
                result["clinical_use_block_reason"] = "retraction_review_required"
        elif plan.record_type == "dailymed_setid":
            payload = self.transport.get_xml(plan.source_id, plan.url)
            record = _dailymed_label_record(payload, str(plan.identifier))
            result["records"] = [record]
            if record["dose_sections_included"]:
                result["limitations"].append(
                    "Directions are from this exact U.S. OTC label only; product/formulation match and the patient medication gate are still required."
                )
            else:
                result["limitations"].append(
                    "Directions are omitted unless the SPL is unambiguously a permitted human OTC label with a recognized marketing category."
                )
        elif plan.record_type == "rxnorm_rxcui":
            payload = self.transport.get_json(plan.source_id, plan.url)
            properties = payload.get("properties", {}) if isinstance(payload, Mapping) else {}
            result["records"] = [_rxnorm_record({**properties, "rxcui": plan.identifier})]
            result["may_support_patient_specific_recommendation"] = False
        else:
            payload = self.transport.get_json(plan.source_id, plan.url)
            result["records"] = [self._trial_record(payload)]
            result["may_support_patient_specific_recommendation"] = False
        result["access_status"] = "retrieved" if result["records"] else "no_records"
        return result

    @staticmethod
    def _dailymed_record(row: Mapping[str, Any]) -> dict[str, Any]:
        setid = str(row.get("setid") or row.get("set_id") or "").lower()
        return {
            "source_owner": SOURCE_REGISTRY["dailymed"].source_owner,
            "canonical_url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}" if setid else SOURCE_REGISTRY["dailymed"].canonical_url,
            "source_kind": "structured_product_label",
            "jurisdiction": "United States",
            "publication_or_version_date": str(row.get("published_date") or "unknown"),
            "retrieved_at": _now(),
            "section_or_record_id": f"DailyMed SETID:{setid}" if setid else "DailyMed record:unknown",
            "freshness": "current",
            "freshness_basis": "current DailyMed API listing at retrieval",
            "freshness_scope": "label_record_only",
            "trust": "official_label_record",
            "limitations": SOURCE_REGISTRY["dailymed"].limitations,
            "title": str(row.get("title") or ""),
            "version": str(row.get("spl_version") or row.get("version") or "unknown"),
        }

    @staticmethod
    def _trial_record(payload: Mapping[str, Any]) -> dict[str, Any]:
        protocol = payload.get("protocolSection", {}) if isinstance(payload, Mapping) else {}
        identification = protocol.get("identificationModule", {}) if isinstance(protocol, Mapping) else {}
        status = protocol.get("statusModule", {}) if isinstance(protocol, Mapping) else {}
        nct_id = str(identification.get("nctId") or "")
        return {
            "source_owner": SOURCE_REGISTRY["clinicaltrials"].source_owner,
            "canonical_url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else SOURCE_REGISTRY["clinicaltrials"].canonical_url,
            "source_kind": "trial_registry",
            "jurisdiction": "international trials",
            "publication_or_version_date": str(status.get("studyFirstPostDateStruct", {}).get("date") or "unknown"),
            "retrieved_at": _now(),
            "section_or_record_id": nct_id or "NCT:unknown",
            "freshness": "current",
            "freshness_basis": "current ClinicalTrials.gov registry record at retrieval",
            "freshness_scope": "registry_status_only",
            "trust": "official_registry_record",
            "limitations": SOURCE_REGISTRY["clinicaltrials"].limitations,
            "title": str(identification.get("briefTitle") or ""),
            "status": str(status.get("overallStatus") or ""),
        }
