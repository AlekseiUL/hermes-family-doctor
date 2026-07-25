from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable
from urllib.parse import urlparse


REGISTRY_VERSION = "family-doctor-sources.v3"


class SourceUrlRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HostPolicy:
    host: str
    path_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class SourceContract:
    source_id: str
    source_owner: str
    canonical_url: str
    source_kind: str
    evidence_role: str
    jurisdiction: str
    purpose: str
    limitations: str
    topics: tuple[str, ...]
    may_support_patient_specific_recommendation: bool
    host_policies: tuple[HostPolicy, ...]
    access_mode: str = "official_public_read_only"
    runtime_access: str = "catalog_only"
    runtime_route: str | None = None

    @property
    def executable_now(self) -> bool:
        return self.runtime_access != "catalog_only"

    @property
    def patient_specific_runtime_available(self) -> bool:
        return self.executable_now and self.may_support_patient_specific_recommendation

    @property
    def connection_state(self) -> str:
        return "connected_no_key" if self.executable_now else "catalog_only_by_design"

    def as_dict(self) -> dict[str, object]:
        return {
            "registry_version": REGISTRY_VERSION,
            "source_id": self.source_id,
            "source_owner": self.source_owner,
            "canonical_url": self.canonical_url,
            "source_kind": self.source_kind,
            "evidence_role": self.evidence_role,
            "jurisdiction": self.jurisdiction,
            "purpose": self.purpose,
            "limitations": self.limitations,
            "topics": list(self.topics),
            "may_support_patient_specific_recommendation": self.may_support_patient_specific_recommendation,
            "patient_specific_runtime_available": self.patient_specific_runtime_available,
            "access_mode": self.access_mode,
            "runtime_access": self.runtime_access,
            "runtime_route": self.runtime_route,
            "executable_now": self.executable_now,
            "connection_state": self.connection_state,
            "credentials_required": False if self.executable_now else None,
        }


def _hp(host: str, *prefixes: str) -> HostPolicy:
    return HostPolicy(host=host, path_prefixes=tuple(prefixes))


_SOURCES = (
    SourceContract(
        "who", "World Health Organization", "https://www.who.int/publications",
        "official_guidance", "guideline", "global",
        "Global clinical and public-health guidance.",
        "Global guidance may be superseded or inapplicable to the patient's setting or individual context.",
        ("guidelines", "prevention"), True,
        (_hp("www.who.int", "/publications", "/news", "/countries", "/health-topics"),),
        runtime_access="runtime_exact_page",
        runtime_route="medical_official_page_fetch",
    ),
    SourceContract(
        "nice", "National Institute for Health and Care Excellence", "https://www.nice.org.uk/guidance",
        "official_guidance", "guideline", "United Kingdom",
        "Clinical and public-health recommendations.",
        "UK pathways, availability and formulary require an applicability check for the patient's setting.",
        ("guidelines", "prevention", "pediatrics"), True,
        (_hp("www.nice.org.uk", "/guidance", "/corporate"),),
    ),
    SourceContract(
        "cdc", "U.S. Centers for Disease Control and Prevention", "https://www.cdc.gov/",
        "official_guidance", "guideline", "United States",
        "Public-health, vaccination and disease guidance.",
        "U.S. schedules and recommendations require an applicability check for the patient's setting; automated access may be blocked.",
        ("guidelines", "prevention", "vaccination"), True,
        (
            _hp("www.cdc.gov", "/"),
            _hp("tools.cdc.gov", "/api/v2/resources/media"),
        ),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search",
    ),
    SourceContract(
        "uspstf", "U.S. Preventive Services Task Force",
        "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/uspstf-a-and-b-recommendations",
        "official_guidance", "guideline", "United States",
        "Evidence-graded prevention and screening recommendations.",
        "Population, grade, release date and care setting must match the patient; U.S. context is not universal policy.",
        ("prevention", "screening", "guidelines"), True,
        (_hp("www.uspreventiveservicestaskforce.org", "/uspstf/"),),
        runtime_access="runtime_exact_page",
        runtime_route="medical_official_page_fetch",
    ),
    SourceContract(
        "rch", "Royal Children's Hospital Melbourne", "https://www.rch.org.au/clinicalguide/",
        "official_guidance", "guideline", "Australia",
        "Open pediatric clinical practice guidance.",
        "Australian setting and local formulations may differ; pediatric care still requires current child context.",
        ("pediatrics", "guidelines"), True,
        (_hp("www.rch.org.au", "/clinicalguide/"),),
        runtime_access="runtime_exact_page",
        runtime_route="medical_official_page_fetch",
    ),
    SourceContract(
        "cochrane", "Cochrane", "https://www.cochranelibrary.com/about/about-cochrane-library",
        "systematic_review", "evidence_synthesis", "international",
        "Systematic-review discovery and accessible review content.",
        "Access and reuse vary; withdrawn or superseded review status must be checked.",
        ("reviews", "literature"), True,
        (_hp("www.cochranelibrary.com", "/about/", "/cdsr/", "/central/"),),
    ),
    SourceContract(
        "pubmed", "U.S. National Library of Medicine", "https://pubmed.ncbi.nlm.nih.gov/",
        "bibliographic_database", "discovery_only", "international literature",
        "Literature discovery by PMID and indexed metadata.",
        "A citation or abstract is not a guideline and may be retracted, superseded or inapplicable.",
        ("literature", "reviews", "guidelines"), False,
        (_hp("pubmed.ncbi.nlm.nih.gov", "/"),),
        runtime_access="runtime_record_output",
        runtime_route="ncbi_eutils:pubmed",
    ),
    SourceContract(
        "ncbi_eutils", "U.S. National Library of Medicine",
        "https://www.ncbi.nlm.nih.gov/books/NBK25497/",
        "official_api", "discovery_only", "international literature",
        "Structured PubMed and NCBI record search and retrieval.",
        "API metadata does not establish clinical applicability; NCBI usage limits apply.",
        ("literature", "reviews", "guidelines"), False,
        (
            _hp("www.ncbi.nlm.nih.gov", "/books/NBK25497/", "/home/develop/api/"),
            _hp("eutils.ncbi.nlm.nih.gov", "/entrez/eutils/"),
        ),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search|medical_record_fetch",
    ),
    SourceContract(
        "pmc", "U.S. National Library of Medicine", "https://pmc.ncbi.nlm.nih.gov/",
        "full_text_archive", "discovery_only", "international literature",
        "Lawfully accessible biomedical full text.",
        "Availability in PMC does not make a paper a guideline or validate it for a patient.",
        ("literature", "reviews"), False,
        (
            _hp("pmc.ncbi.nlm.nih.gov", "/"),
            _hp("eutils.ncbi.nlm.nih.gov", "/entrez/eutils/"),
        ),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search|medical_record_fetch",
    ),
    SourceContract(
        "dailymed", "U.S. National Library of Medicine", "https://dailymed.nlm.nih.gov/dailymed/",
        "structured_product_label", "official_label", "United States",
        "Current U.S. Structured Product Label records and version history.",
        "Product and jurisdiction specific; not a prescription or complete interaction check.",
        ("labels", "medications"), True,
        (_hp("dailymed.nlm.nih.gov", "/dailymed/"),),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search|medical_record_fetch",
    ),
    SourceContract(
        "openfda", "U.S. Food and Drug Administration", "https://open.fda.gov/apis/drug/label/",
        "regulatory_api", "label_fallback", "United States",
        "Machine-readable FDA label-submission fallback.",
        "FDA says not to rely on openFDA for medical-care decisions; records may be incomplete or non-current.",
        ("labels", "medications"), False,
        (
            _hp("open.fda.gov", "/apis/drug/label/"),
            _hp("api.fda.gov", "/drug/label.json"),
        ),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search",
    ),
    SourceContract(
        "rxnorm", "U.S. National Library of Medicine", "https://lhncbc.nlm.nih.gov/RxNav/",
        "drug_terminology", "terminology_only", "United States terminology",
        "Normalize drug identities, concepts and classes.",
        "RxNorm identity is not interaction clearance, prescribing guidance or proof of local availability.",
        ("identity", "medications", "terminology"), False,
        (
            _hp("lhncbc.nlm.nih.gov", "/RxNav/"),
            _hp("rxnav.nlm.nih.gov", "/REST/"),
        ),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search|medical_record_fetch",
    ),
    SourceContract(
        "ema", "European Medicines Agency", "https://www.ema.europa.eu/en/medicines",
        "regulatory_source", "official_label", "European Union",
        "EU centrally authorized medicine assessments and safety information.",
        "EU authorization and availability do not establish prescribing rules or supply in another setting.",
        ("labels", "medications", "safety"), True,
        (
            _hp("www.ema.europa.eu", "/en/medicines", "/en/news", "/en/human-regulatory-overview"),
            _hp("epi.developer.ema.europa.eu", "/api-details", "/product"),
        ),
        runtime_access="runtime_exact_page",
        runtime_route="medical_official_page_fetch",
    ),
    SourceContract(
        "lactmed", "U.S. National Library of Medicine", "https://www.ncbi.nlm.nih.gov/books/NBK501922/",
        "reference_database", "specialist_reference", "international literature",
        "Drug and lactation reference with literature links.",
        "Requires product, dose, infant and maternal context plus clinician/pharmacist review.",
        ("medications", "pregnancy_lactation"), True,
        (
            _hp("www.ncbi.nlm.nih.gov", "/books/NBK501922/", "/books/NBK", "/books/n/lactmed/"),
            _hp("eutils.ncbi.nlm.nih.gov", "/entrez/eutils/"),
        ),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search|medical_official_page_fetch",
    ),
    SourceContract(
        "clinicaltrials", "U.S. National Library of Medicine", "https://clinicaltrials.gov/",
        "trial_registry", "discovery_only", "international trials",
        "Trial registration, status and results discovery.",
        "Registration is not proof of efficacy, safety or suitability and cannot drive treatment advice.",
        ("trials",), False,
        (_hp("clinicaltrials.gov", "/", "/api/v2/", "/data-api/"),),
        runtime_access="runtime_query_source",
        runtime_route="medical_evidence_search|medical_record_fetch",
    ),
    SourceContract(
        "loinc", "Regenstrief Institute", "https://loinc.org/",
        "clinical_terminology", "terminology_only", "international terminology",
        "Normalize laboratory and clinical observation identifiers.",
        "LOINC identifies an observation but does not interpret a result.",
        ("terminology", "labs"), False,
        (_hp("loinc.org", "/"),),
    ),
    SourceContract(
        "snomed_ct", "SNOMED International", "https://www.snomed.org/",
        "clinical_terminology", "terminology_only", "licensed terminology",
        "Clinical terminology and concept relationships.",
        "Licensing and territory rules apply; terminology is not a diagnostic engine.",
        ("terminology",), False,
        (_hp("www.snomed.org", "/"),),
    ),
    SourceContract(
        "icd11", "World Health Organization", "https://icd.who.int/",
        "classification", "classification_only", "global",
        "WHO disease classification and coding reference.",
        "A classification code is not a diagnosis or patient-specific recommendation.",
        ("terminology", "classification"), False,
        (_hp("icd.who.int", "/"),),
    ),
    SourceContract(
        "hl7_fhir", "Health Level Seven International", "https://hl7.org/fhir/",
        "interoperability_standard", "interoperability_only", "international",
        "Health-data exchange structures and validation.",
        "FHIR is not a clinical evidence library and does not grant EHR access.",
        ("interoperability",), False,
        (_hp("hl7.org", "/fhir/"),),
    ),
)


SOURCE_ORDER = tuple(item.source_id for item in _SOURCES)
SOURCE_REGISTRY = MappingProxyType({item.source_id: item for item in _SOURCES})


def _policy_for_host(contract: SourceContract, host: str) -> HostPolicy | None:
    return next((policy for policy in contract.host_policies if policy.host == host), None)


def validate_url_for_source(source_id: str, url: str, *, allow_query: bool = False) -> None:
    contract = SOURCE_REGISTRY.get(source_id)
    if contract is None:
        raise SourceUrlRejected("unknown_source")
    if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
        raise SourceUrlRejected("invalid_url")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SourceUrlRejected("https_required")
    if parsed.username is not None or parsed.password is not None:
        raise SourceUrlRejected("userinfo_forbidden")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceUrlRejected("invalid_port") from exc
    if port is not None:
        raise SourceUrlRejected("explicit_port_forbidden")
    host = (parsed.hostname or "").casefold()
    policy = _policy_for_host(contract, host)
    if policy is None:
        raise SourceUrlRejected("host_not_allowlisted")
    if parsed.fragment:
        raise SourceUrlRejected("fragment_forbidden")
    if parsed.query and not allow_query:
        raise SourceUrlRejected("query_forbidden")
    path = parsed.path or "/"
    lowered = path.casefold()
    if "\\" in path or "%" in path or "//" in path or any(marker in lowered for marker in ("..", "%2f", "%5c", "%2e", "%00")):
        raise SourceUrlRejected("ambiguous_path")
    if not any(path.startswith(prefix) for prefix in policy.path_prefixes):
        raise SourceUrlRejected("path_not_allowlisted")


def source_catalog(topics: Iterable[str] | None = None) -> list[dict[str, object]]:
    selected = {str(topic).strip() for topic in (topics or ()) if str(topic).strip()}
    return [
        SOURCE_REGISTRY[source_id].as_dict()
        for source_id in SOURCE_ORDER
        if not selected or selected.intersection(SOURCE_REGISTRY[source_id].topics)
    ]
