from __future__ import annotations

import json
import socket
import unittest
from urllib.parse import parse_qs, urlparse

from family_health.evidence_service import EvidenceService
from family_health.query_planner import QueryRejected, plan_record, plan_search
from family_health.retriever import RetrievalError, SafeHttpTransport
from family_health.source_registry import (
    SOURCE_ORDER,
    SOURCE_REGISTRY,
    SourceUrlRejected,
    source_catalog,
    validate_url_for_source,
)


class FakeTransport:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.pubmed_retracted = False
        self.dailymed_label_mode = "unknown"

    def get_json(self, source_id: str, url: str) -> dict:
        self.calls.append((source_id, url))
        parsed = urlparse(url)
        if parsed.path.endswith("/esearch.fcgi"):
            database = parse_qs(parsed.query).get("db", ["pubmed"])[0]
            if database == "pmc":
                return {"esearchresult": {"idlist": ["9990001"]}}
            if database == "books":
                return {"esearchresult": {"idlist": ["4569360"]}}
            return {"esearchresult": {"idlist": ["12345678"]}}
        if parsed.path.endswith("/esummary.fcgi"):
            database = parse_qs(parsed.query).get("db", ["pubmed"])[0]
            if database == "pmc":
                return {
                    "result": {
                        "uids": ["9990001"],
                        "9990001": {
                            "uid": "9990001",
                            "title": "Synthetic open-access review",
                            "pubdate": "2025",
                            "sortfirstauthor": "Example",
                            "fulljournalname": "Synthetic Journal",
                            "articleids": [{"idtype": "pmcid", "value": "PMC9990001"}],
                        },
                    }
                }
            if database == "books":
                return {
                    "result": {
                        "uids": ["4569360"],
                        "4569360": {
                            "uid": "4569360",
                            "title": "Acetaminophen",
                            "pubdate": "2026",
                            "chapteraccessionid": "NBK501194",
                            "bookname": "Drugs and Lactation Database (LactMed)",
                        },
                    }
                }
            return {
                "result": {
                    "uids": ["12345678"],
                    "12345678": {
                        "uid": "12345678",
                        "title": "Synthetic guideline record",
                        "pubdate": "2025",
                        "sortfirstauthor": "Example",
                        "fulljournalname": "Synthetic Journal",
                        "pubtype": ["Practice Guideline"],
                    },
                }
            }
        if parsed.path.endswith("/spls.json"):
            return {
                "data": [{
                    "setid": "11111111-2222-3333-4444-555555555555",
                    "spl_version": "7",
                    "published_date": "Jan 01, 2026",
                    "title": "SYNTHETIC LABEL",
                }]
            }
        if parsed.path.endswith("/approximateTerm.json"):
            return {
                "approximateGroup": {
                    "candidate": [{"rxcui": "5640", "name": "ibuprofen", "score": "100"}]
                }
            }
        if parsed.netloc == "api.fda.gov":
            return {
                "meta": {"results": {"total": 1}},
                "results": [{
                    "id": "synthetic-openfda-label",
                    "effective_time": "20260101",
                    "openfda": {
                        "substance_name": ["ACETAMINOPHEN"],
                        "brand_name": ["Synthetic brand"],
                    },
                    "warnings": ["Synthetic warning."],
                    "contraindications": ["Synthetic contraindication."],
                    "drug_interactions": ["Synthetic interaction section."],
                    "dosage_and_administration": ["This must never be exposed."],
                }],
            }
        if parsed.netloc == "tools.cdc.gov":
            return {
                "results": [{
                    "id": "100",
                    "name": "Old influenza guidance",
                    "description": "Historical public-health information.",
                    "sourceUrl": "https://www.cdc.gov/old/index.html",
                    "datePublished": "2014-01-01",
                    "dateModified": "2014-02-01",
                }, {
                    "id": "12345",
                    "name": "Synthetic influenza guidance",
                    "description": "Current public-health information.",
                    "sourceUrl": "https://www.cdc.gov/flu/about/index.html",
                    "datePublished": "2025-10-01",
                    "dateModified": "2026-01-15",
                }]
            }
        if "/api/v2/studies/" in parsed.path:
            return {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": "NCT00000001",
                        "briefTitle": "Synthetic trial",
                    },
                    "statusModule": {"overallStatus": "COMPLETED"},
                }
            }
        raise AssertionError(f"unexpected fake URL: {url}")

    def get_xml(self, source_id: str, url: str) -> bytes:
        self.calls.append((source_id, url))
        parsed = urlparse(url)
        if parsed.path.endswith("/efetch.fcgi"):
            database = parse_qs(parsed.query).get("db", ["pubmed"])[0]
            if database == "pmc":
                return b"""<?xml version='1.0'?>
                <article article-type='review-article'>
                  <front><journal-meta><journal-title-group><journal-title>Synthetic Journal</journal-title></journal-title-group></journal-meta>
                    <article-meta><article-id pub-id-type='pmc'>PMC9990001</article-id>
                    <title-group><article-title>Synthetic open-access review</article-title></title-group>
                    <pub-date pub-type='epub'><year>2025</year><month>04</month><day>03</day></pub-date>
                    <abstract><p>Synthetic full-text abstract.</p></abstract></article-meta></front>
                  <body><sec><title>Results</title><p>Bounded synthetic article body.</p></sec></body>
                </article>"""
            payload = b"""<?xml version='1.0'?>
            <PubmedArticleSet><PubmedArticle><MedlineCitation>
              <PMID>12345678</PMID><Article>
                <Journal><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue><Title>Synthetic Journal</Title></Journal>
                <ArticleTitle>Synthetic guideline record</ArticleTitle>
                <Abstract><AbstractText Label='RESULTS'>Synthetic indexed abstract.</AbstractText></Abstract>
                <AuthorList><Author><LastName>Example</LastName><Initials>A</Initials></Author></AuthorList>
                <PublicationTypeList><PublicationType>Practice Guideline</PublicationType></PublicationTypeList>
              </Article></MedlineCitation><PubmedData><ArticleIdList>
                <ArticleId IdType='doi'>10.0000/synthetic</ArticleId>
              </ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""
            if self.pubmed_retracted:
                payload = payload.replace(
                    b"</MedlineCitation>",
                    b"<CommentsCorrections RefType='RetractedPublication'/></MedlineCitation>",
                )
            return payload
        if "/services/v2/spls/" in parsed.path and parsed.path.endswith(".xml"):
            if self.dailymed_label_mode == "otc":
                document_code = "<code code='34390-5' displayName='HUMAN OTC DRUG LABEL'/>"
                approval = "<subjectOf><approval><code code='C200263' displayName='OTC Monograph Drug'/></approval></subjectOf>"
            elif self.dailymed_label_mode == "prescription":
                document_code = "<code code='34391-3' displayName='HUMAN PRESCRIPTION DRUG LABEL'/>"
                approval = "<subjectOf><approval><code code='C73594' displayName='NDA'/></approval></subjectOf>"
            elif self.dailymed_label_mode == "unapproved_otc":
                document_code = "<code code='34390-5' displayName='HUMAN OTC DRUG LABEL'/>"
                approval = "<subjectOf><approval><code code='C73627' displayName='Unapproved drug other'/></approval></subjectOf>"
            else:
                document_code = ""
                approval = ""
            return f"""<?xml version='1.0'?>
            <document xmlns='urn:hl7-org:v3'>
              {document_code}
              <title>SYNTHETIC LABEL</title>
              <setId root='11111111-2222-3333-4444-555555555555'/>
              <versionNumber value='7'/><effectiveTime value='20260101'/>
              <component><structuredBody>
                <component><section><code code='48780-1'/><subject><manufacturedProduct><manufacturedProduct>
                  <code code='00000-0001'/><name>Synthetic acetaminophen</name><formCode displayName='TABLET'/>
                  <ingredient classCode='ACTIB'><quantity><numerator value='500' unit='mg'/><denominator value='1' unit='1'/></quantity>
                    <ingredientSubstance><name>ACETAMINOPHEN</name></ingredientSubstance></ingredient>
                </manufacturedProduct>{approval}</manufacturedProduct></subject></section></component>
                <component><section><code code='34070-3'/><title>CONTRAINDICATIONS</title><text>Synthetic contraindication text.</text></section></component>
                <component><section><code code='34073-7'/><title>DRUG INTERACTIONS</title><text>Synthetic interaction text.</text></section></component>
                <component><section><code code='34068-7'/><title>Directions</title><text>Take two tablets every six hours; maximum six tablets in 24 hours.</text></section></component>
              </structuredBody></component>
            </document>""".encode()
        raise AssertionError(f"unexpected fake XML URL: {url}")

    def get_text(self, source_id: str, url: str) -> tuple[str, str]:
        self.calls.append((source_id, url))
        return (
            """<html><head><title>Synthetic RCH page</title><script>ignore me</script></head>
            <body><nav>navigation noise</nav><main><h1>Synthetic pediatric guideline</h1>
            <p>Reviewed clinical guidance.</p><h2>Return precautions</h2>
            <p>Seek current professional review when needed.</p></main></body></html>""",
            url,
        )


class SourceRegistryTests(unittest.TestCase):
    def test_registry_has_exact_stable_nineteen_source_surface(self):
        self.assertEqual(19, len(SOURCE_ORDER))
        self.assertEqual(len(SOURCE_ORDER), len(set(SOURCE_ORDER)))
        self.assertEqual(set(SOURCE_ORDER), set(SOURCE_REGISTRY))
        for source_id in SOURCE_ORDER:
            contract = SOURCE_REGISTRY[source_id]
            self.assertEqual(source_id, contract.source_id)
            validate_url_for_source(source_id, contract.canonical_url)

    def test_catalog_separates_clinical_and_non_clinical_roles(self):
        rows = {row["source_id"]: row for row in source_catalog()}
        self.assertEqual("guideline", rows["nice"]["evidence_role"])
        self.assertEqual("official_label", rows["dailymed"]["evidence_role"])
        self.assertEqual("discovery_only", rows["clinicaltrials"]["evidence_role"])
        self.assertEqual("terminology_only", rows["loinc"]["evidence_role"])
        self.assertEqual("interoperability_only", rows["hl7_fhir"]["evidence_role"])
        self.assertFalse(rows["clinicaltrials"]["may_support_patient_specific_recommendation"])
        self.assertEqual("catalog_only", rows["nice"]["runtime_access"])
        self.assertFalse(rows["nice"]["patient_specific_runtime_available"])
        self.assertTrue(rows["dailymed"]["executable_now"])
        self.assertTrue(rows["dailymed"]["patient_specific_runtime_available"])
        self.assertEqual("runtime_record_output", rows["pubmed"]["runtime_access"])

    def test_registry_marks_connected_no_key_and_catalog_only_sources_honestly(self):
        rows = {row["source_id"]: row for row in source_catalog()}
        connected = {
            source_id for source_id, row in rows.items()
            if row["connection_state"] == "connected_no_key"
        }
        self.assertEqual({
            "who", "cdc", "uspstf", "rch", "pubmed", "ncbi_eutils", "pmc",
            "dailymed", "openfda", "rxnorm", "ema", "lactmed", "clinicaltrials",
        }, connected)
        for source_id in {"nice", "cochrane", "loinc", "snomed_ct", "icd11", "hl7_fhir"}:
            self.assertEqual("catalog_only_by_design", rows[source_id]["connection_state"])
            self.assertFalse(rows[source_id]["executable_now"])
        self.assertFalse(rows["openfda"]["may_support_patient_specific_recommendation"])

    def test_url_gate_rejects_authority_and_path_confusion(self):
        bad = (
            "http://www.cdc.gov/",
            "https://www.cdc.gov.evil.example/",
            "https://user@www.cdc.gov/",
            "https://www.cdc.gov:444/",
            "https://www.cdc.gov/%2e%2e/private",
            "https://www.cdc.gov/a%2fb",
            "https://www.cdc.gov/?patient=synthetic",
            "https://www.cdc.gov/#fragment",
        )
        for url in bad:
            with self.subTest(url=url), self.assertRaises(SourceUrlRejected):
                validate_url_for_source("cdc", url)


class StructuredPlannerTests(unittest.TestCase):
    def test_search_accepts_only_controlled_concepts_not_raw_text(self):
        plan = plan_search({
            "route": "guidelines",
            "concept_id": "hypertension",
            "population": "adult",
            "max_results": 3,
        })
        self.assertEqual("ncbi_eutils", plan.source_id)
        query = parse_qs(urlparse(plan.url).query)
        self.assertIn("Hypertension", query["term"][0])
        self.assertNotIn("patient", query["term"][0].casefold())

        symptom = plan_search({"route": "literature", "concept_id": "chest_pain"})
        self.assertIn("Chest Pain", parse_qs(urlparse(symptom.url).query)["term"][0])

        rejected = (
            {"route": "guidelines", "query": "Patient Name hypertension"},
            {"route": "guidelines", "concept_id": "Patient Name hypertension"},
            {"route": "guidelines", "concept_id": "hypertension", "patient_id": "synthetic"},
            {"route": "guidelines", "concept_id": "hypertension", "date_of_birth": "1980-01-01"},
        )
        for request in rejected:
            with self.subTest(request=request), self.assertRaises(QueryRejected):
                plan_search(request)

    def test_medication_and_trial_routes_use_controlled_or_typed_identifiers(self):
        label = plan_search({"route": "labels", "ingredient_id": "ibuprofen", "max_results": 2})
        self.assertEqual("dailymed", label.source_id)
        self.assertEqual(["ibuprofen"], parse_qs(urlparse(label.url).query)["drug_name"])

        identity = plan_search({"route": "identity", "ingredient_id": "ibuprofen", "max_results": 2})
        self.assertEqual("rxnorm", identity.source_id)

        record = plan_record({"record_type": "clinical_trial_nct", "identifier": "NCT00000001"})
        self.assertEqual("clinicaltrials", record.source_id)
        self.assertFalse(record.may_support_patient_specific_recommendation)

        for identifier in ("NCT123", "../NCT00000001", "NCT00000001?x=1"):
            with self.subTest(identifier=identifier), self.assertRaises(QueryRejected):
                plan_record({"record_type": "clinical_trial_nct", "identifier": identifier})

        label_record = plan_record({
            "record_type": "dailymed_setid",
            "identifier": "11111111-2222-3333-4444-555555555555",
        })
        self.assertTrue(urlparse(label_record.url).path.endswith(".xml"))
        pubmed_record = plan_record({"record_type": "pubmed_pmid", "identifier": "12345678"})
        self.assertTrue(urlparse(pubmed_record.url).path.endswith("/efetch.fcgi"))
        self.assertEqual(["xml"], parse_qs(urlparse(pubmed_record.url).query)["retmode"])

    def test_new_no_key_routes_use_only_controlled_public_terms(self):
        pmc = plan_search({"route": "full_text", "concept_id": "asthma", "max_results": 2})
        self.assertEqual("pmc", pmc.source_id)
        self.assertEqual(["pmc"], parse_qs(urlparse(pmc.url).query)["db"])
        self.assertIn("open access", parse_qs(urlparse(pmc.url).query)["term"][0].casefold())

        lactmed = plan_search({"route": "lactation", "ingredient_id": "acetaminophen"})
        self.assertEqual("lactmed", lactmed.source_id)
        lactmed_query = parse_qs(urlparse(lactmed.url).query)
        self.assertEqual(["books"], lactmed_query["db"])
        self.assertIn("lactmed[book]", lactmed_query["term"][0].casefold())

        fallback = plan_search({"route": "label_fallback", "ingredient_id": "acetaminophen"})
        self.assertEqual("openfda", fallback.source_id)
        self.assertIn("openfda.substance_name", parse_qs(urlparse(fallback.url).query)["search"][0])

        cdc = plan_search({"route": "public_health", "concept_id": "influenza"})
        self.assertEqual("cdc", cdc.source_id)
        cdc_query = parse_qs(urlparse(cdc.url).query)
        self.assertEqual(["Influenza"], cdc_query["topic"])
        self.assertEqual(
            ["id,name,description,sourceUrl,targetUrl,datePublished,dateModified"],
            cdc_query["fields"],
        )


class EvidenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.service = EvidenceService(self.transport)

    def test_pubmed_search_returns_record_level_provenance_and_limits(self):
        result = self.service.search({
            "route": "guidelines", "concept_id": "hypertension",
            "population": "adult", "max_results": 1,
        })
        self.assertTrue(result["success"])
        self.assertEqual("PMID:12345678", result["records"][0]["section_or_record_id"])
        self.assertEqual("https://pubmed.ncbi.nlm.nih.gov/12345678/", result["records"][0]["canonical_url"])
        self.assertEqual("unknown", result["records"][0]["freshness"])
        self.assertIn("bibliographic metadata", " ".join(result["limitations"]).casefold())
        self.assertEqual(2, len(self.transport.calls))

    def test_label_and_identity_results_do_not_claim_complete_interaction_check(self):
        label = self.service.search({"route": "labels", "ingredient_id": "ibuprofen", "max_results": 1})
        self.assertEqual("official_label", label["evidence_role"])
        self.assertFalse(label["complete_interaction_check"])
        self.assertIn("11111111-2222-3333-4444-555555555555", json.dumps(label))

        identity = self.service.search({"route": "identity", "ingredient_id": "ibuprofen", "max_results": 1})
        self.assertEqual("terminology_only", identity["evidence_role"])
        self.assertFalse(identity["complete_interaction_check"])
        self.assertEqual("U.S. National Library of Medicine", identity["records"][0]["source_owner"])
        self.assertEqual("official_terminology_record", identity["records"][0]["trust"])

    def test_exact_pubmed_record_includes_bounded_indexed_abstract(self):
        result = self.service.fetch_record({"record_type": "pubmed_pmid", "identifier": "12345678"})
        record = result["records"][0]
        self.assertEqual("PMID:12345678", record["section_or_record_id"])
        self.assertEqual("RESULTS: Synthetic indexed abstract.", record["abstract"])
        self.assertFalse(result["may_support_patient_specific_recommendation"])
        self.assertEqual("untrusted_official_data", record["content_classification"])
        self.assertEqual("unknown", record["freshness"])
        self.assertEqual("discovery_only", record["clinical_claim_status"])

    def test_pubmed_retraction_signal_blocks_clinical_use(self):
        self.transport.pubmed_retracted = True
        result = self.service.fetch_record({"record_type": "pubmed_pmid", "identifier": "12345678"})
        record = result["records"][0]
        self.assertTrue(record["retraction_review_required"])
        self.assertEqual("blocked_retraction_review", record["clinical_claim_status"])
        self.assertTrue(result["clinical_use_blocked"])

    def test_exact_dailymed_record_returns_safety_sections_without_dosage(self):
        result = self.service.fetch_record({
            "record_type": "dailymed_setid",
            "identifier": "11111111-2222-3333-4444-555555555555",
        })
        record = result["records"][0]
        names = {section["section_name"] for section in record["sections"]}
        self.assertEqual({"contraindications", "drug_interactions"}, names)
        self.assertFalse(record["dose_sections_included"])
        self.assertNotIn("maximum six tablets", json.dumps(record))
        self.assertFalse(record["complete_interaction_check"])

    def test_exact_permitted_otc_record_returns_bounded_label_directions_and_product_identity(self):
        self.transport.dailymed_label_mode = "otc"
        result = self.service.fetch_record({
            "record_type": "dailymed_setid",
            "identifier": "11111111-2222-3333-4444-555555555555",
        })
        record = result["records"][0]
        self.assertTrue(record["nonprescription_label_confirmed"])
        self.assertTrue(record["dose_sections_included"])
        self.assertEqual("34068-7", record["label_directions"]["section_code"])
        self.assertEqual("exact_product_label", record["directions_source_scope"])
        self.assertFalse(record["patient_specific_dose_calculated"])
        self.assertEqual("TABLET", record["products"][0]["dosage_form"])
        self.assertEqual("500", record["products"][0]["active_ingredients"][0]["strength_numerator"])

    def test_prescription_and_unapproved_otc_labels_never_expose_directions(self):
        for mode in ("prescription", "unapproved_otc"):
            with self.subTest(mode=mode):
                self.transport.dailymed_label_mode = mode
                result = self.service.fetch_record({
                    "record_type": "dailymed_setid",
                    "identifier": "11111111-2222-3333-4444-555555555555",
                })
                record = result["records"][0]
                self.assertFalse(record["nonprescription_label_confirmed"])
                self.assertFalse(record["dose_sections_included"])
                self.assertIsNone(record["label_directions"])
                self.assertNotIn("maximum six tablets", json.dumps(record))

    def test_trial_record_is_discovery_not_treatment_evidence(self):
        result = self.service.fetch_record({
            "record_type": "clinical_trial_nct", "identifier": "NCT00000001",
        })
        self.assertTrue(result["success"])
        self.assertEqual("discovery_only", result["evidence_role"])
        self.assertFalse(result["may_support_patient_specific_recommendation"])

    def test_exact_official_page_is_bounded_inert_and_provenanced(self):
        result = self.service.fetch_official_page({
            "source_id": "rch",
            "url": "https://www.rch.org.au/clinicalguide/guideline_index/Synthetic/",
            "record_id": "RCH:Synthetic",
        })
        record = result["records"][0]
        self.assertEqual("RCH:Synthetic", record["section_or_record_id"])
        self.assertEqual("Synthetic pediatric guideline", record["title"])
        self.assertIn("Reviewed clinical guidance", record["text"])
        self.assertNotIn("ignore me", json.dumps(record))
        self.assertNotIn("navigation noise", json.dumps(record))
        self.assertEqual("untrusted_official_data", record["content_classification"])

    def test_cdc_exact_page_is_not_advertised_when_only_the_api_is_reliable(self):
        with self.assertRaisesRegex(QueryRejected, "source_has_no_exact_page_route"):
            self.service.fetch_official_page({
                "source_id": "cdc",
                "url": "https://www.cdc.gov/flu/about/index.html",
                "record_id": "CDC:flu-about",
            })

    def test_pmc_search_returns_typed_full_text_discovery_records(self):
        result = self.service.search({
            "route": "full_text", "concept_id": "asthma", "max_results": 1,
        })
        record = result["records"][0]
        self.assertEqual("PMC:PMC9990001", record["section_or_record_id"])
        self.assertEqual("https://pmc.ncbi.nlm.nih.gov/articles/PMC9990001/", record["canonical_url"])
        self.assertFalse(result["may_support_patient_specific_recommendation"])

    def test_lactmed_search_returns_exact_bookshelf_page_not_clinical_clearance(self):
        result = self.service.search({
            "route": "lactation", "ingredient_id": "acetaminophen", "max_results": 1,
        })
        record = result["records"][0]
        self.assertEqual("LactMed:NBK501194", record["section_or_record_id"])
        self.assertEqual("https://www.ncbi.nlm.nih.gov/books/NBK501194/", record["canonical_url"])
        self.assertFalse(result["complete_interaction_check"])
        self.assertIn("maternal", " ".join(result["limitations"]).casefold())

    def test_openfda_fallback_omits_dose_and_cannot_drive_treatment(self):
        result = self.service.search({
            "route": "label_fallback", "ingredient_id": "acetaminophen", "max_results": 1,
        })
        serialized = json.dumps(result)
        self.assertIn("Synthetic warning", serialized)
        self.assertNotIn("must never be exposed", serialized)
        self.assertFalse(result["may_support_patient_specific_recommendation"])
        self.assertEqual("fallback_only", result["clinical_use"])

    def test_cdc_search_returns_official_public_health_page_as_guidance_not_diagnosis(self):
        result = self.service.search({
            "route": "public_health", "concept_id": "influenza", "max_results": 1,
        })
        record = result["records"][0]
        self.assertEqual("https://www.cdc.gov/flu/about/index.html", record["canonical_url"])
        self.assertEqual("public_health_guidance", record["source_kind"])
        self.assertFalse(record["diagnostic_claim"])

    def test_exact_pmc_record_is_bounded_and_stays_discovery_only(self):
        result = self.service.fetch_record({
            "record_type": "pmc_pmcid", "identifier": "PMC9990001",
        })
        record = result["records"][0]
        self.assertEqual("PMC:PMC9990001", record["section_or_record_id"])
        self.assertEqual("Synthetic full-text abstract.", record["abstract"])
        self.assertIn("Bounded synthetic article body.", record["body"])
        self.assertFalse(result["may_support_patient_specific_recommendation"])
        self.assertEqual("untrusted_official_data", record["content_classification"])


class FakeHeaders(dict):
    def get_content_type(self):
        return str(self.get("Content-Type", "")).split(";", 1)[0].strip().casefold()


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "application/json", location: str | None = None):
        self._body = body
        self.status = status
        self.headers = FakeHeaders({"Content-Type": content_type})
        if location:
            self.headers["Location"] = location
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, *responses: FakeResponse):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected network call")
        return self.responses.pop(0)


def public_resolver(host, port, *, type):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class SafeTransportTests(unittest.TestCase):
    def test_get_json_is_https_get_without_auth_cookie_or_body(self):
        opener = FakeOpener(FakeResponse(b'{"ok": true}'))
        transport = SafeHttpTransport(opener=opener, resolver=public_resolver, sleep=lambda _: None)
        payload = transport.get_json(
            "ncbi_eutils",
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=pubmed&retmode=json",
        )
        self.assertEqual({"ok": True}, payload)
        request, timeout = opener.requests[0]
        self.assertEqual("GET", request.get_method())
        self.assertIsNone(request.data)
        headers = {key.casefold(): value for key, value in request.header_items()}
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertLessEqual(timeout, 15)

    def test_private_dns_answer_is_rejected_before_request(self):
        def private_resolver(host, port, *, type):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        opener = FakeOpener(FakeResponse(b"{}"))
        transport = SafeHttpTransport(opener=opener, resolver=private_resolver, sleep=lambda _: None)
        with self.assertRaisesRegex(RetrievalError, "non_public_dns_answer"):
            transport.get_json("clinicaltrials", "https://clinicaltrials.gov/api/v2/version")
        self.assertEqual([], opener.requests)

    def test_get_xml_is_bounded_and_requires_xml_content(self):
        opener = FakeOpener(FakeResponse(b"<document/>", content_type="application/xml"))
        transport = SafeHttpTransport(opener=opener, resolver=public_resolver, sleep=lambda _: None)
        payload = transport.get_xml(
            "dailymed",
            "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/11111111-2222-3333-4444-555555555555.xml",
        )
        self.assertEqual(b"<document/>", payload)
        request, _timeout = opener.requests[0]
        self.assertEqual("*/*", dict(request.header_items())["Accept"])

        html = FakeOpener(FakeResponse(b"<html></html>", content_type="text/html"))
        with self.assertRaisesRegex(RetrievalError, "xml_content_type_required"):
            SafeHttpTransport(opener=html, resolver=public_resolver, sleep=lambda _: None).get_xml(
                "dailymed",
                "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/11111111-2222-3333-4444-555555555555.xml",
            )

    def test_get_text_reads_only_allowlisted_official_html(self):
        opener = FakeOpener(FakeResponse(
            b"<html><main><h1>Synthetic guideline</h1></main></html>",
            content_type="text/html",
        ))
        transport = SafeHttpTransport(opener=opener, resolver=public_resolver, sleep=lambda _: None)
        text, final_url = transport.get_text(
            "rch",
            "https://www.rch.org.au/clinicalguide/guideline_index/Synthetic/",
        )
        self.assertIn("Synthetic guideline", text)
        self.assertEqual(
            "https://www.rch.org.au/clinicalguide/guideline_index/Synthetic/",
            final_url,
        )
        request, _timeout = opener.requests[0]
        self.assertIn("text/html", dict(request.header_items())["Accept"])

    def test_redirect_is_revalidated_and_cannot_escape_source_contract(self):
        opener = FakeOpener(FakeResponse(b"", status=302, location="https://evil.example/steal"))
        transport = SafeHttpTransport(opener=opener, resolver=public_resolver, sleep=lambda _: None)
        with self.assertRaisesRegex(RetrievalError, "redirect_rejected"):
            transport.get_json("clinicaltrials", "https://clinicaltrials.gov/api/v2/version")

    def test_size_and_content_type_fail_closed(self):
        too_large = FakeOpener(FakeResponse(b"x" * 65, content_type="application/json"))
        with self.assertRaisesRegex(RetrievalError, "response_too_large"):
            SafeHttpTransport(
                opener=too_large, resolver=public_resolver, max_bytes=64, sleep=lambda _: None,
            ).get_json("clinicaltrials", "https://clinicaltrials.gov/api/v2/version")

        html = FakeOpener(FakeResponse(b"<html></html>", content_type="text/html"))
        with self.assertRaisesRegex(RetrievalError, "json_content_type_required"):
            SafeHttpTransport(opener=html, resolver=public_resolver, sleep=lambda _: None).get_json(
                "clinicaltrials", "https://clinicaltrials.gov/api/v2/version",
            )


if __name__ == "__main__":
    unittest.main()
