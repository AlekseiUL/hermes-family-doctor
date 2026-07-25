from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse


OFFICIAL_SOURCE_REGISTRY: dict[str, dict[str, str]] = {
    "pubmed": {"owner": "NCBI", "host": "pubmed.ncbi.nlm.nih.gov", "kind": "bibliographic_record"},
    "dailymed": {"owner": "U.S. National Library of Medicine", "host": "dailymed.nlm.nih.gov", "kind": "official_drug_label"},
    "rxnorm": {"owner": "U.S. National Library of Medicine", "host": "rxnav.nlm.nih.gov", "kind": "terminology"},
    "openfda": {"owner": "U.S. FDA", "host": "api.fda.gov", "kind": "label_fallback"},
    "clinicaltrials": {"owner": "U.S. National Library of Medicine", "host": "clinicaltrials.gov", "kind": "trial_registry"},
    "who": {"owner": "World Health Organization", "host": "www.who.int", "kind": "official_guidance"},
    "nice": {"owner": "NICE", "host": "www.nice.org.uk", "kind": "official_guidance"},
    "uspstf": {"owner": "USPSTF", "host": "www.uspreventiveservicestaskforce.org", "kind": "official_guidance"},
    "cdc": {"owner": "U.S. CDC", "host": "www.cdc.gov", "kind": "official_guidance"},
    "rch": {"owner": "Royal Children's Hospital Melbourne", "host": "www.rch.org.au", "kind": "official_guidance"},
    "ema": {"owner": "European Medicines Agency", "host": "www.ema.europa.eu", "kind": "official_regulatory"},
    "loinc": {"owner": "Regenstrief Institute", "host": "loinc.org", "kind": "terminology"},
}
ALLOWLISTED_HOSTS = frozenset(item["host"] for item in OFFICIAL_SOURCE_REGISTRY.values())
REQUIRED_ENVELOPE_FIELDS = (
    "source_owner", "canonical_url", "source_kind", "jurisdiction",
    "publication_or_version_date", "retrieved_at", "section_or_record_id",
    "freshness", "trust", "limitations",
)
INSTRUCTION_MARKERS = (
    "ignore previous", "system prompt", "tool call", "execute command", "run shell",
    "change config", "write memory", "read file", "delete file",
)


@dataclass(frozen=True)
class EvidenceValidation:
    accepted: bool
    codes: tuple[str, ...]
    content_classification: str = "untrusted_data"

    def as_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "codes": list(self.codes), "content_classification": self.content_classification}


def validate_evidence_envelope(
    envelope: Mapping[str, Any], *, content: str = "", current_evidence_required: bool = True
) -> EvidenceValidation:
    codes: set[str] = set()
    for field in REQUIRED_ENVELOPE_FIELDS:
        if envelope.get(field) in (None, ""):
            codes.add(f"missing:{field}")
    parsed = urlparse(str(envelope.get("canonical_url", "")))
    if parsed.scheme != "https":
        codes.add("invalid:non_https_url")
    if parsed.hostname not in ALLOWLISTED_HOSTS:
        codes.add("invalid:host_not_allowlisted")
    if envelope.get("section_or_record_id") in (None, ""):
        codes.add("invalid:missing_record_provenance")
    if current_evidence_required and envelope.get("freshness") in (None, "", "stale", "unknown"):
        codes.add("invalid:current_evidence_required")
    lowered = content.casefold()
    if any(marker in lowered for marker in INSTRUCTION_MARKERS):
        codes.add("invalid:untrusted_instruction_attempt")
    return EvidenceValidation(not codes, tuple(sorted(codes)))
