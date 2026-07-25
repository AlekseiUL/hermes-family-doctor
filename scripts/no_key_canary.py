#!/usr/bin/env python3
"""Bounded manual canaries for the 13 credential-free source contracts."""
from __future__ import annotations

import json
from typing import Callable

from family_health.evidence_service import EvidenceService
from family_health.retriever import SafeHttpTransport


def nonempty_result(value: dict) -> bool:
    return bool(value.get("success") and value.get("records"))


def main() -> int:
    transport = SafeHttpTransport(max_bytes=2_000_000)
    service = EvidenceService(transport)

    def exact(source_id: str, url: str) -> bool:
        text, _final = transport.get_text(source_id, url)
        return bool(text.strip())

    checks: dict[str, Callable[[], bool]] = {
        "who": lambda: exact("who", "https://www.who.int/publications"),
        "cdc": lambda: nonempty_result(service.search({"route": "public_health", "concept_id": "influenza", "max_results": 1})),
        "uspstf": lambda: exact("uspstf", "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/uspstf-a-and-b-recommendations"),
        "rch": lambda: exact("rch", "https://www.rch.org.au/clinicalguide/"),
        "pubmed": lambda: exact("pubmed", "https://pubmed.ncbi.nlm.nih.gov/38900001/"),
        "ncbi_eutils": lambda: bool(transport.get_json("ncbi_eutils", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=pubmed&retmode=json")),
        "pmc": lambda: nonempty_result(service.search({"route": "full_text", "concept_id": "asthma", "max_results": 1})),
        "dailymed": lambda: nonempty_result(service.search({"route": "labels", "ingredient_id": "ibuprofen", "max_results": 1})),
        "openfda": lambda: nonempty_result(service.search({"route": "label_fallback", "ingredient_id": "acetaminophen", "max_results": 1})),
        "rxnorm": lambda: nonempty_result(service.search({"route": "identity", "ingredient_id": "ibuprofen", "max_results": 1})),
        "ema": lambda: exact("ema", "https://www.ema.europa.eu/en/medicines"),
        "lactmed": lambda: nonempty_result(service.search({"route": "lactation", "ingredient_id": "acetaminophen", "max_results": 1})),
        "clinicaltrials": lambda: nonempty_result(service.search({"route": "trials", "concept_id": "asthma", "max_results": 1})),
    }
    rows = []
    for source_id, check in checks.items():
        try:
            retrieved = bool(check())
            rows.append({"source_id": source_id, "status": "retrieved" if retrieved else "empty"})
        except Exception as exc:
            rows.append({"source_id": source_id, "status": "degraded", "error_class": type(exc).__name__})
    summary = {
        "total": len(rows),
        "retrieved": sum(row["status"] == "retrieved" for row in rows),
        "degraded": sum(row["status"] != "retrieved" for row in rows),
        "results": rows,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["retrieved"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
