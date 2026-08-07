from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core import utils
from core.models import Folder, PDFFile
from core.utils import SearchAnswerLanguageError, SearchDataIntegrityError, SearchHit


class SmallTalkClassificationTests(SimpleTestCase):
    def test_complete_conversational_queries_are_classified(self):
        cases = {
            "Hello!": "greeting",
            "  THANK YOU  ": "gratitude",
            "Who are you?": "identity",
            "What is today?": "live_information",
            "नमस्कार!": "greeting",
            "धन्यवाद": "gratitude",
            "तुम्ही कोण आहात?": "identity",
            "आप कौन हैं?": "identity",
        }

        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(utils.classify_small_talk_query(query), expected)
                self.assertTrue(utils.is_general_query(query))

    def test_domain_queries_never_match_conversational_substrings(self):
        queries = [
            "give me most updated rules about societies",
            "what is this rule",
            "membership rules",
            "historical society rules",
            "candidate eligibility under the Act",
            "mandate for society elections",
            "timeline for registration",
            "Hi, what is Rule 79?",
            "Thank you for explaining Rule 79",
        ]

        for query in queries:
            with self.subTest(query=query):
                self.assertIsNone(utils.classify_small_talk_query(query))
                self.assertFalse(utils.is_general_query(query))


class AnswerLanguageResolutionTests(SimpleTestCase):
    def test_question_language_overrides_page_language(self):
        self.assertEqual(
            utils.resolve_answer_language("सभासदाचे अधिकार काय आहेत?", "en"),
            "mr",
        )
        self.assertEqual(
            utils.resolve_answer_language("What are a member's rights?", "mr"),
            "en",
        )

    def test_non_language_query_uses_page_language_as_fallback(self):
        self.assertEqual(utils.resolve_answer_language("79?", "mr"), "mr")
        self.assertEqual(utils.resolve_answer_language("79?", "en"), "en")


class GeneratedAnswerLanguageContractTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Mock()

    @staticmethod
    def _response(answer):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=answer))]
        )

    def test_marathi_question_builds_marathi_only_prompt(self):
        self.client.chat.completions.create.return_value = self._response(
            "सभासदाला संस्थेच्या नोंदी पाहण्याचा अधिकार आहे."
        )
        with patch("core.utils._get_client", return_value=self.client), patch(
            "core.ai_guard.get_ai_cache_scope",
            return_value="test-provider",
        ):
            answer = utils.generate_gpt_answer(
                "सभासदाचे अधिकार काय आहेत?",
                "Member rights context",
                references=[{"title": "Society Act"}],
                language="en",
            )

        self.assertIn("सभासदाला", answer)
        messages = self.client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("उत्तर फक्त मराठीत", messages[0]["content"])
        self.assertIn("स्रोत", messages[1]["content"])

    def test_wrong_language_is_repaired_once_before_caching(self):
        diagnostics = {}
        self.client.chat.completions.create.side_effect = [
            self._response("A member may inspect the society records."),
            self._response("सभासद संस्थेच्या नोंदी पाहू शकतो."),
        ]
        with patch("core.utils._get_client", return_value=self.client), patch(
            "core.ai_guard.get_ai_cache_scope",
            return_value="test-provider-repair",
        ), self.assertLogs("core.utils", level="WARNING"):
            answer = utils.generate_gpt_answer(
                "सभासदाचे अधिकार काय आहेत?",
                "Member rights context",
                language="en",
                diagnostics=diagnostics,
            )

        self.assertEqual(answer, "सभासद संस्थेच्या नोंदी पाहू शकतो.")
        self.assertEqual(self.client.chat.completions.create.call_count, 2)
        self.assertEqual(diagnostics["chat_calls"], 2)

    def test_repeated_wrong_language_fails_closed(self):
        self.client.chat.completions.create.side_effect = [
            self._response("First English answer."),
            self._response("Second English answer."),
        ]
        with patch("core.utils._get_client", return_value=self.client), patch(
            "core.ai_guard.get_ai_cache_scope",
            return_value="test-provider-fail-closed",
        ), self.assertLogs("core.utils", level="WARNING"), self.assertRaises(
            SearchAnswerLanguageError
        ):
            utils.generate_gpt_answer(
                "सभासदाचे अधिकार काय आहेत?",
                "Member rights context",
                language="en",
            )


class SearchFolderOrchestrationTests(SimpleTestCase):
    def setUp(self):
        self.first = SimpleNamespace(pk=1)
        self.second = SimpleNamespace(pk=2)

    @patch("core.utils.generate_gpt_answer", return_value="answer")
    @patch("core.utils.retrieve_folder_hits")
    @patch("core.utils.create_query_embedding", return_value=np.array([1.0, 0.0]))
    def test_one_embedding_and_one_answer_cover_all_folders(
        self,
        create_embedding,
        retrieve_hits,
        generate_answer,
    ):
        retrieve_hits.side_effect = [
            [
                SearchHit(
                    pdf_id=10,
                    title="First",
                    folder_id=1,
                    folder="One",
                    uploaded_at=None,
                    snippet="exact first evidence",
                    score=0.8,
                )
            ],
            [
                SearchHit(
                    pdf_id=20,
                    title="Second",
                    folder_id=2,
                    folder="Two",
                    uploaded_at=None,
                    snippet="exact second evidence",
                    score=0.9,
                )
            ],
        ]

        answer, references, diagnostics = utils.search_pdf_folders(
            [(self.first, None), (self.second, None)],
            "question",
            folder_scores={1: 0.9},
        )

        self.assertEqual(answer, "answer")
        self.assertEqual([item["pdf_id"] for item in references], [20, 10])
        self.assertEqual(diagnostics["folders_scanned"], 2)
        create_embedding.assert_called_once()
        self.assertEqual(create_embedding.call_args.args, ("question",))
        self.assertIs(create_embedding.call_args.kwargs["diagnostics"], diagnostics)
        self.assertEqual(retrieve_hits.call_count, 2)
        generate_answer.assert_called_once()
        context = generate_answer.call_args.kwargs["context"]
        self.assertIn("exact first evidence", context)
        self.assertIn("exact second evidence", context)

    @patch("core.utils.generate_gpt_answer", return_value="answer")
    @patch("core.utils.create_query_embedding", return_value=np.array([1.0, 0.0]))
    def test_one_corrupt_folder_does_not_hide_valid_evidence(
        self,
        _create_embedding,
        _generate_answer,
    ):
        valid_hit = SearchHit(
            pdf_id=20,
            title="Valid",
            folder_id=2,
            folder="Two",
            uploaded_at=None,
            snippet="valid evidence",
            score=0.9,
        )
        with patch(
            "core.utils.retrieve_folder_hits",
            side_effect=[SearchDataIntegrityError("corrupt"), [valid_hit]],
        ):
            answer, references, diagnostics = utils.search_pdf_folders(
                [(self.first, None), (self.second, None)],
                "question",
            )

        self.assertEqual(answer, "answer")
        self.assertEqual([item["pdf_id"] for item in references], [20])
        self.assertEqual(diagnostics["integrity_failures"], 1)

    @patch("core.utils.create_query_embedding", return_value=np.array([1.0, 0.0]))
    @patch(
        "core.utils.retrieve_folder_hits",
        side_effect=SearchDataIntegrityError("corrupt"),
    )
    def test_all_corrupt_folders_fail_closed(self, _retrieve_hits, _create_embedding):
        with self.assertRaisesRegex(SearchDataIntegrityError, "All authorized"):
            utils.search_pdf_folders(
                [(self.first, None), (self.second, None)],
                "question",
            )

    def test_folder_limit_fails_before_provider_call(self):
        folders = [(SimpleNamespace(pk=index), None) for index in range(65)]
        with patch("core.utils.create_query_embedding") as create_embedding:
            with self.assertRaisesRegex(SearchDataIntegrityError, "folder limit"):
                utils.search_pdf_folders(folders, "question")
        create_embedding.assert_not_called()


class SearchHitOwnershipTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="search-hit-owner",
            password="test-password",
            role="admin",
        )
        self.folder = Folder.objects.create(name="Duplicate evidence", created_by=self.user)

    def test_duplicate_title_and_chunk_keep_exact_pdf_identity(self):
        first = PDFFile.objects.create(
            title="Duplicate",
            file="pdfs/first.pdf",
            folder=self.folder,
            uploaded_by=self.user,
            page_chunks=["shared chunk"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        second = PDFFile.objects.create(
            title="Duplicate",
            file="pdfs/second.pdf",
            folder=self.folder,
            uploaded_by=self.user,
            page_chunks=["shared chunk"],
            chunk_embeddings=[[0.0, 1.0]],
        )
        matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        with patch(
            "core.utils.build_or_load_faiss_index_for_folder",
            return_value=(None, ["shared chunk", "shared chunk"], matrix),
        ), patch(
            "core.utils._search_chunk_indices_with_faiss_or_numpy",
            return_value=[(1, 0.9)],
        ):
            hits = utils.retrieve_folder_hits(
                self.folder,
                "question",
                query_embedding=np.array([0.0, 1.0], dtype=np.float32),
            )

        self.assertEqual(first.title, second.title)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].pdf_id, second.pk)

    def test_embedding_mutation_during_retrieval_fails_closed(self):
        PDFFile.objects.create(
            title="Mutable",
            file="pdfs/mutable.pdf",
            folder=self.folder,
            uploaded_by=self.user,
            page_chunks=["stored evidence"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        stale_matrix = np.array([[0.0, 1.0]], dtype=np.float32)
        with patch(
            "core.utils.build_or_load_faiss_index_for_folder",
            return_value=(None, ["stored evidence"], stale_matrix),
        ):
            with self.assertRaisesRegex(SearchDataIntegrityError, "changed during retrieval"):
                utils.retrieve_folder_hits(
                    self.folder,
                    "question",
                    query_embedding=np.array([1.0, 0.0], dtype=np.float32),
                )


@override_settings(
    PUBLIC_SEARCH_ENABLED=True,
    PUBLIC_SEARCH_ALL_FOLDERS=True,
    PUBLIC_SEARCH_FOLDER_IDS=frozenset(),
)
class PublicSearchRoutingViewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="public-routing-owner",
            password="test-password",
            role="admin",
        )
        self.first = Folder.objects.create(name="Act", created_by=self.owner)
        self.second = Folder.objects.create(name="Circulars", created_by=self.owner)

    def _search(self, *, detected, references):
        diagnostics = {
            "folders_scanned": 2,
            "integrity_failures": 0,
            "candidates": len(references),
        }
        with patch("core.views.classify_small_talk_query", return_value=None), patch(
            "core.views._public_search_rate_limited",
            return_value=False,
        ), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=detected,
        ), patch(
            "core.views.search_pdf_folders",
            return_value=("answer" if references else "", references, diagnostics),
        ) as search_folders:
            response = self.client.post(reverse("search_query"), {"query": "question"})
        return response, search_folders

    def test_no_keyword_match_searches_every_visible_folder(self):
        reference = {
            "title": "Circular",
            "pdf_id": 2,
            "folder": self.second.name,
            "uploaded_at": None,
            "score": 0.9,
        }
        response, search_folders = self._search(detected=[], references=[reference])

        self.assertEqual(response.status_code, 200)
        scopes = search_folders.call_args.args[0]
        self.assertEqual([folder.pk for folder, _pdfs in scopes], [self.first.pk, self.second.pk])
        self.assertTrue(all(pdfs is None for _folder, pdfs in scopes))
        self.assertEqual(response.json()["kind"], "evidence_answer")

    def test_keyword_match_ranks_first_but_does_not_exclude_other_folders(self):
        response, search_folders = self._search(
            detected=[(self.second, 0.9)],
            references=[],
        )

        self.assertEqual(response.status_code, 200)
        scopes = search_folders.call_args.args[0]
        self.assertEqual([folder.pk for folder, _pdfs in scopes], [self.second.pk, self.first.pk])

    def test_no_evidence_response_gives_actionable_guidance(self):
        response, _search_folders = self._search(detected=[], references=[])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kind"], "no_evidence")
        self.assertEqual(response.json()["references"], [])
        self.assertIn("No supporting source", response.json()["answer"])

    def test_exact_small_talk_skips_retrieval_and_records_typed_outcome(self):
        with patch(
            "core.views._public_search_rate_limited",
            return_value=False,
        ), patch("core.views.detect_folder_by_keywords_multi") as detect_folders, patch(
            "core.views.search_pdf_folders"
        ) as search_folders, self.assertLogs("core.views", level="INFO") as captured:
            response = self.client.post(reverse("search_query"), {"query": "Hello!"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kind"], "small_talk")
        self.assertEqual(response.json()["references"], [])
        detect_folders.assert_not_called()
        search_folders.assert_not_called()
        logs = "\n".join(captured.output)
        self.assertIn("search_completed route=small_talk outcome=greeting", logs)
        self.assertNotIn("Hello", logs)

    def test_marathi_small_talk_uses_question_language_on_english_page(self):
        with patch("core.views._public_search_rate_limited", return_value=False):
            response = self.client.post(
                reverse("search_query"),
                {"query": "नमस्कार", "language": "en"},
            )

        payload = response.json()
        self.assertEqual(payload["kind"], "small_talk")
        self.assertEqual(payload["language"], "mr")
        self.assertIn("नमस्कार", payload["answer"])

    def test_marathi_document_query_overrides_english_page_language(self):
        diagnostics = {
            "folders_scanned": 2,
            "integrity_failures": 0,
            "candidates": 1,
        }
        reference = {
            "title": "Act",
            "pdf_id": 1,
            "folder": self.first.name,
            "uploaded_at": None,
            "score": 0.9,
        }
        with patch("core.views._public_search_rate_limited", return_value=False), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[],
        ), patch(
            "core.views.search_pdf_folders",
            return_value=("सभासदाला हा अधिकार आहे.", [reference], diagnostics),
        ) as search_folders:
            response = self.client.post(
                reverse("search_query"),
                {"query": "सभासदाचे अधिकार काय आहेत?", "language": "en"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["language"], "mr")
        self.assertEqual(search_folders.call_args.kwargs["language"], "mr")

    def test_marathi_no_evidence_guidance_is_localized(self):
        diagnostics = {
            "folders_scanned": 2,
            "integrity_failures": 0,
            "candidates": 0,
        }
        with patch("core.views._public_search_rate_limited", return_value=False), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[],
        ), patch(
            "core.views.search_pdf_folders",
            return_value=("", [], diagnostics),
        ):
            response = self.client.post(
                reverse("search_query"),
                {"query": "सभासदाचे अधिकार काय आहेत?", "language": "en"},
            )

        payload = response.json()
        self.assertEqual(payload["kind"], "no_evidence")
        self.assertEqual(payload["language"], "mr")
        self.assertIn("स्रोत सापडला नाही", payload["answer"])

    def test_provider_language_mismatch_returns_explicit_error(self):
        with patch("core.views._public_search_rate_limited", return_value=False), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[],
        ), patch(
            "core.views.search_pdf_folders",
            side_effect=SearchAnswerLanguageError("wrong language"),
        ):
            response = self.client.post(
                reverse("search_query"),
                {"query": "सभासदाचे अधिकार काय आहेत?", "language": "en"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "answer_language_mismatch")

    def test_updated_rules_query_reaches_document_search(self):
        diagnostics = {
            "folders_scanned": 2,
            "integrity_failures": 0,
            "candidates": 0,
        }
        with patch(
            "core.views._public_search_rate_limited",
            return_value=False,
        ), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[],
        ), patch(
            "core.views.search_pdf_folders",
            return_value=("", [], diagnostics),
        ) as search_folders:
            response = self.client.post(
                reverse("search_query"),
                {"query": "give me most updated rules about societies"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kind"], "no_evidence")
        search_folders.assert_called_once()

    def test_search_telemetry_never_records_query_content(self):
        with self.assertLogs("core.views", level="INFO") as captured:
            response, _search_folders = self._search(detected=[], references=[])

        self.assertEqual(response.status_code, 200)
        logs = "\n".join(captured.output)
        self.assertIn("search_completed route=global", logs)
        self.assertNotIn("question", logs)

    def test_public_counter_uses_public_visibility_scope(self):
        public_pdf = PDFFile.objects.create(
            title="Public",
            file="pdfs/public.pdf",
            folder=self.first,
            uploaded_by=self.owner,
            indexed=True,
        )
        private_folder = Folder.objects.create(name="Private", created_by=self.owner)
        PDFFile.objects.create(
            title="Private",
            file="pdfs/private.pdf",
            folder=private_folder,
            uploaded_by=self.owner,
            indexed=True,
        )

        with override_settings(
            PUBLIC_SEARCH_ALL_FOLDERS=False,
            PUBLIC_SEARCH_FOLDER_IDS=frozenset({self.first.pk}),
        ):
            response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["indexed_count"], 1)
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(public_pdf.folder_id, self.first.pk)

    def test_explicit_public_scope_never_passes_hidden_folder_to_search(self):
        with override_settings(
            PUBLIC_SEARCH_ALL_FOLDERS=False,
            PUBLIC_SEARCH_FOLDER_IDS=frozenset({self.second.pk}),
        ), patch("core.views.classify_small_talk_query", return_value=None), patch(
            "core.views._public_search_rate_limited",
            return_value=False,
        ), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[],
        ) as detect_folders, patch(
            "core.views.search_pdf_folders",
            return_value=(
                "",
                [],
                {"folders_scanned": 1, "integrity_failures": 0, "candidates": 0},
            ),
        ) as search_folders:
            response = self.client.post(reverse("search_query"), {"query": "question"})

        self.assertEqual(response.status_code, 200)
        detected_scope = detect_folders.call_args.kwargs["folders"]
        self.assertEqual([folder.pk for folder in detected_scope], [self.second.pk])
        search_scope = search_folders.call_args.args[0]
        self.assertEqual([folder.pk for folder, _pdfs in search_scope], [self.second.pk])
