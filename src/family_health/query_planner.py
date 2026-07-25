from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlencode

from .source_registry import SOURCE_REGISTRY, validate_url_for_source


class QueryRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


CLINICAL_CONCEPTS = MappingProxyType({
    "allergic_rhinitis": "Rhinitis, Allergic",
    "anemia": "Anemia",
    "anxiety": "Anxiety Disorders",
    "asthma": "Asthma",
    "atopic_dermatitis": "Dermatitis, Atopic",
    "atrial_fibrillation": "Atrial Fibrillation",
    "bronchiolitis": "Bronchiolitis",
    "chest_pain": "Chest Pain",
    "chronic_kidney_disease": "Renal Insufficiency, Chronic",
    "constipation": "Constipation",
    "copd": "Pulmonary Disease, Chronic Obstructive",
    "coronary_artery_disease": "Coronary Artery Disease",
    "cough": "Cough",
    "covid19": "COVID-19",
    "croup": "Croup",
    "depression": "Depressive Disorder",
    "diarrhea": "Diarrhea",
    "dizziness": "Dizziness",
    "dyspnea": "Dyspnea",
    "fatigue": "Fatigue",
    "fever": "Fever",
    "gastroenteritis": "Gastroenteritis",
    "gerd": "Gastroesophageal Reflux",
    "heart_failure": "Heart Failure",
    "headache": "Headache",
    "hyperlipidemia": "Hyperlipidemias",
    "hypertension": "Hypertension",
    "hyperthyroidism": "Hyperthyroidism",
    "hypothyroidism": "Hypothyroidism",
    "ibd": "Inflammatory Bowel Diseases",
    "ibs": "Irritable Bowel Syndrome",
    "influenza": "Influenza, Human",
    "insomnia": "Sleep Initiation and Maintenance Disorders",
    "low_back_pain": "Low Back Pain",
    "migraine": "Migraine Disorders",
    "obesity": "Obesity",
    "osteoarthritis": "Osteoarthritis",
    "osteoporosis": "Osteoporosis",
    "otitis_media": "Otitis Media",
    "peptic_ulcer": "Peptic Ulcer",
    "psoriasis": "Psoriasis",
    "palpitations": "Palpitations",
    "rheumatoid_arthritis": "Arthritis, Rheumatoid",
    "type2_diabetes": "Diabetes Mellitus, Type 2",
    "syncope": "Syncope",
    "urinary_tract_infection": "Urinary Tract Infections",
    "vaccination": "Vaccination",
})


MEDICATION_INGREDIENTS = MappingProxyType({
    "acetaminophen": "acetaminophen",
    "albuterol": "albuterol",
    "alprazolam": "alprazolam",
    "amiodarone": "amiodarone",
    "amlodipine": "amlodipine",
    "amoxicillin": "amoxicillin",
    "apixaban": "apixaban",
    "aspirin": "aspirin",
    "atorvastatin": "atorvastatin",
    "azithromycin": "azithromycin",
    "budesonide": "budesonide",
    "carvedilol": "carvedilol",
    "cetirizine": "cetirizine",
    "ciprofloxacin": "ciprofloxacin",
    "clarithromycin": "clarithromycin",
    "clonazepam": "clonazepam",
    "clopidogrel": "clopidogrel",
    "dabigatran": "dabigatran",
    "diclofenac": "diclofenac",
    "digoxin": "digoxin",
    "doxycycline": "doxycycline",
    "fluoxetine": "fluoxetine",
    "furosemide": "furosemide",
    "gabapentin": "gabapentin",
    "hydrochlorothiazide": "hydrochlorothiazide",
    "ibuprofen": "ibuprofen",
    "levothyroxine": "levothyroxine",
    "lisinopril": "lisinopril",
    "lithium": "lithium",
    "loratadine": "loratadine",
    "losartan": "losartan",
    "meloxicam": "meloxicam",
    "metformin": "metformin",
    "methotrexate": "methotrexate",
    "metoprolol": "metoprolol",
    "montelukast": "montelukast",
    "naproxen": "naproxen",
    "omeprazole": "omeprazole",
    "pantoprazole": "pantoprazole",
    "prednisone": "prednisone",
    "rivaroxaban": "rivaroxaban",
    "rosuvastatin": "rosuvastatin",
    "sertraline": "sertraline",
    "spironolactone": "spironolactone",
    "tramadol": "tramadol",
    "trimethoprim_sulfamethoxazole": "trimethoprim sulfamethoxazole",
    "warfarin": "warfarin",
    "zolpidem": "zolpidem",
})


POPULATIONS = MappingProxyType({
    "general": None,
    "adult": '"Adult"[MeSH Terms]',
    "child": '"Child"[MeSH Terms]',
    "older_adult": '"Aged"[MeSH Terms]',
    "pregnancy": '"Pregnancy"[MeSH Terms]',
})


CDC_TOPICS = MappingProxyType({
    "covid19": "COVID-19",
    "diarrhea": "Diarrhea",
    "influenza": "Influenza",
    "obesity": "Obesity",
    "type2_diabetes": "Diabetes",
    "vaccination": "Vaccination",
})


@dataclass(frozen=True)
class QueryPlan:
    route: str
    source_id: str
    url: str
    evidence_role: str
    may_support_patient_specific_recommendation: bool
    max_results: int = 1
    concept_id: str | None = None
    ingredient_id: str | None = None
    record_type: str | None = None
    identifier: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "source_id": self.source_id,
            "url": self.url,
            "evidence_role": self.evidence_role,
            "may_support_patient_specific_recommendation": self.may_support_patient_specific_recommendation,
            "max_results": self.max_results,
            "concept_id": self.concept_id,
            "ingredient_id": self.ingredient_id,
            "record_type": self.record_type,
            "identifier": self.identifier,
        }


def _strict_keys(request: Mapping[str, Any], allowed: set[str]) -> None:
    extras = set(request) - allowed
    if extras:
        raise QueryRejected("unsupported_or_private_field")


def _max_results(value: Any) -> int:
    if value is None:
        return 3
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise QueryRejected("invalid_max_results")
    return value


def _plan(source_id: str, route: str, url: str, **kwargs: Any) -> QueryPlan:
    validate_url_for_source(source_id, url, allow_query=True)
    source = SOURCE_REGISTRY[source_id]
    return QueryPlan(
        route=route,
        source_id=source_id,
        url=url,
        evidence_role=source.evidence_role,
        may_support_patient_specific_recommendation=source.may_support_patient_specific_recommendation,
        **kwargs,
    )


def plan_search(request: Mapping[str, Any]) -> QueryPlan:
    if not isinstance(request, Mapping):
        raise QueryRejected("request_must_be_object")
    route = request.get("route")
    if route in {"guidelines", "reviews", "literature", "trials", "full_text"}:
        _strict_keys(request, {"route", "concept_id", "population", "max_results"})
        concept_id = request.get("concept_id")
        if not isinstance(concept_id, str) or concept_id not in CLINICAL_CONCEPTS:
            raise QueryRejected("unknown_controlled_concept")
        population = request.get("population", "general")
        if population not in POPULATIONS:
            raise QueryRejected("unknown_population")
        max_results = _max_results(request.get("max_results"))
        term = f'"{CLINICAL_CONCEPTS[concept_id]}"[MeSH Terms]'
        if route == "guidelines":
            term += " AND (guideline[Publication Type] OR practice guideline[Publication Type])"
        elif route == "reviews":
            term += " AND systematic review[Publication Type]"
        population_term = POPULATIONS[population]
        if population_term:
            term += f" AND {population_term}"
        if route == "trials":
            query = urlencode({"query.cond": CLINICAL_CONCEPTS[concept_id], "pageSize": max_results, "format": "json"})
            return _plan(
                "clinicaltrials", route, f"https://clinicaltrials.gov/api/v2/studies?{query}",
                max_results=max_results, concept_id=concept_id,
            )
        if route == "full_text":
            term += " AND open access[filter]"
            query = urlencode({
                "db": "pmc", "term": term, "retmode": "json",
                "retmax": max_results, "sort": "date", "tool": "hermes_family_doctor_local",
            })
            return _plan(
                "pmc", route,
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{query}",
                max_results=max_results, concept_id=concept_id,
            )
        query = urlencode({
            "db": "pubmed", "term": term, "retmode": "json",
            "retmax": max_results, "sort": "date", "tool": "hermes_family_doctor_local",
        })
        return _plan(
            "ncbi_eutils", route,
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{query}",
            max_results=max_results, concept_id=concept_id,
        )
    if route in {"labels", "identity", "lactation", "label_fallback"}:
        _strict_keys(request, {"route", "ingredient_id", "max_results"})
        ingredient_id = request.get("ingredient_id")
        if not isinstance(ingredient_id, str) or ingredient_id not in MEDICATION_INGREDIENTS:
            raise QueryRejected("unknown_controlled_ingredient")
        max_results = _max_results(request.get("max_results"))
        ingredient = MEDICATION_INGREDIENTS[ingredient_id]
        if route == "lactation":
            term = f'"{ingredient}"[Title] AND lactmed[book]'
            query = urlencode({
                "db": "books", "term": term, "retmode": "json",
                "retmax": max_results, "tool": "hermes_family_doctor_local",
            })
            return _plan(
                "lactmed", route,
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{query}",
                max_results=max_results, ingredient_id=ingredient_id,
            )
        if route == "label_fallback":
            query = urlencode({
                "search": f"openfda.substance_name:{ingredient}",
                "limit": max_results,
            })
            return _plan(
                "openfda", route,
                f"https://api.fda.gov/drug/label.json?{query}",
                max_results=max_results, ingredient_id=ingredient_id,
            )
        if route == "labels":
            query = urlencode({"drug_name": ingredient, "pagesize": max_results, "page": 1})
            return _plan(
                "dailymed", route,
                f"https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?{query}",
                max_results=max_results, ingredient_id=ingredient_id,
            )
        query = urlencode({"term": ingredient, "maxEntries": max_results, "option": 1})
        return _plan(
            "rxnorm", route, f"https://rxnav.nlm.nih.gov/REST/approximateTerm.json?{query}",
            max_results=max_results, ingredient_id=ingredient_id,
        )
    if route == "public_health":
        _strict_keys(request, {"route", "concept_id", "max_results"})
        concept_id = request.get("concept_id")
        if not isinstance(concept_id, str) or concept_id not in CDC_TOPICS:
            raise QueryRejected("controlled_concept_has_no_cdc_topic")
        max_results = _max_results(request.get("max_results"))
        query = urlencode({
            "topic": CDC_TOPICS[concept_id],
            "max": max_results,
            "fields": "id,name,description,sourceUrl,targetUrl,datePublished,dateModified",
        })
        return _plan(
            "cdc", route, f"https://tools.cdc.gov/api/v2/resources/media?{query}",
            max_results=max_results, concept_id=concept_id,
        )
    raise QueryRejected("unknown_route")


_RECORD_PATTERNS = {
    "pubmed_pmid": re.compile(r"[1-9][0-9]{0,9}\Z"),
    "pmc_pmcid": re.compile(r"PMC[1-9][0-9]{0,9}\Z"),
    "dailymed_setid": re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"),
    "rxnorm_rxcui": re.compile(r"[1-9][0-9]{0,10}\Z"),
    "clinical_trial_nct": re.compile(r"NCT[0-9]{8}\Z"),
}


def plan_record(request: Mapping[str, Any]) -> QueryPlan:
    if not isinstance(request, Mapping):
        raise QueryRejected("request_must_be_object")
    _strict_keys(request, {"record_type", "identifier"})
    record_type = request.get("record_type")
    identifier = request.get("identifier")
    if record_type not in _RECORD_PATTERNS or not isinstance(identifier, str):
        raise QueryRejected("unknown_record_type")
    if not _RECORD_PATTERNS[record_type].fullmatch(identifier):
        raise QueryRejected("invalid_record_identifier")
    if record_type == "pubmed_pmid":
        query = urlencode({"db": "pubmed", "id": identifier, "retmode": "xml", "tool": "hermes_family_doctor_local"})
        return _plan(
            "ncbi_eutils", "record",
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{query}",
            max_results=1, record_type=record_type, identifier=identifier,
        )
    if record_type == "pmc_pmcid":
        query = urlencode({"db": "pmc", "id": identifier, "retmode": "xml", "tool": "hermes_family_doctor_local"})
        return _plan(
            "pmc", "record",
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{query}",
            max_results=1, record_type=record_type, identifier=identifier,
        )
    if record_type == "dailymed_setid":
        return _plan(
            "dailymed", "record",
            f"https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{identifier.lower()}.xml",
            max_results=1, record_type=record_type, identifier=identifier.lower(),
        )
    if record_type == "rxnorm_rxcui":
        return _plan(
            "rxnorm", "record", f"https://rxnav.nlm.nih.gov/REST/rxcui/{identifier}/properties.json",
            max_results=1, record_type=record_type, identifier=identifier,
        )
    return _plan(
        "clinicaltrials", "record", f"https://clinicaltrials.gov/api/v2/studies/{identifier}",
        max_results=1, record_type=record_type, identifier=identifier,
    )


def pubmed_summary_url(pmids: list[str]) -> str:
    if not pmids or any(not _RECORD_PATTERNS["pubmed_pmid"].fullmatch(pmid) for pmid in pmids):
        raise QueryRejected("invalid_pubmed_result_ids")
    query = urlencode({
        "db": "pubmed", "id": ",".join(pmids[:5]), "retmode": "json",
        "tool": "hermes_family_doctor_local",
    })
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{query}"
    validate_url_for_source("ncbi_eutils", url, allow_query=True)
    return url


def ncbi_summary_url(source_id: str, database: str, identifiers: list[str]) -> str:
    allowed = {
        ("pmc", "pmc"),
        ("lactmed", "books"),
    }
    if (source_id, database) not in allowed:
        raise QueryRejected("unsupported_ncbi_summary_route")
    if not identifiers or any(not re.fullmatch(r"[1-9][0-9]{0,10}", item) for item in identifiers):
        raise QueryRejected("invalid_ncbi_result_ids")
    query = urlencode({
        "db": database,
        "id": ",".join(identifiers[:5]),
        "retmode": "json",
        "tool": "hermes_family_doctor_local",
    })
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{query}"
    validate_url_for_source(source_id, url, allow_query=True)
    return url
