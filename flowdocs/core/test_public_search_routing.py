from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core import utils
from core.models import Folder, PDFFile
from core.utils import SearchDataIntegrityError, SearchHit


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
        create_embedding.assert_called_once_with("question")
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
        with patch("core.views.is_general_query", return_value=False), patch(
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
        self.assertEqual(response.json()["references"], [])
        self.assertIn("No supporting source", response.json()["answer"])

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
        ), patch("core.views.is_general_query", return_value=False), patch(
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
