from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from core import utils
from core.models import Folder, PDFFile


SIGNED_DIGEST = "a" * 64
POINTER_DIGEST = "b" * 64
SIGNED_RUNTIME = SimpleNamespace(
    generation_id="generation-1",
    manifest_digest=SIGNED_DIGEST,
    pointer_digest=POINTER_DIGEST,
)


@override_settings(
    ACTIVE_RUNTIME=SIGNED_RUNTIME,
    RUNTIME_GENERATION_ID="generation-1",
    RUNTIME_MANIFEST_DIGEST=SIGNED_DIGEST,
)
class SignedSearchPerformanceTests(TestCase):
    def setUp(self):
        cache.clear()
        utils._runtime_search_corpus = None
        self.user = get_user_model().objects.create_user(
            username="search-performance-owner",
            password="test-password",
            role="admin",
        )
        self.public_folder = Folder.objects.create(
            name="Public evidence",
            created_by=self.user,
        )
        self.hidden_folder = Folder.objects.create(
            name="Hidden evidence",
            created_by=self.user,
        )
        PDFFile.objects.create(
            title="Authorized document",
            file="pdfs/authorized.pdf",
            folder=self.public_folder,
            uploaded_by=self.user,
            page_chunks=["authorized source text"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        PDFFile.objects.create(
            title="Hidden document",
            file="pdfs/hidden.pdf",
            folder=self.hidden_folder,
            uploaded_by=self.user,
            page_chunks=["hidden source text"],
            chunk_embeddings=[[0.0, 1.0]],
        )

    def tearDown(self):
        utils._runtime_search_corpus = None
        cache.clear()
        super().tearDown()

    @patch("core.utils.generate_gpt_answer", return_value="Authorized answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_signed_corpus_is_reused_and_filters_unauthorized_folders(
        self,
        create_embedding,
        generate_answer,
    ):
        first_answer, first_references, first_diagnostics = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
        )
        second_answer, second_references, second_diagnostics = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "registration rules",
        )

        self.assertEqual(first_answer, "Authorized answer")
        self.assertEqual(second_answer, "Authorized answer")
        self.assertEqual(
            [reference["title"] for reference in first_references],
            ["Authorized document"],
        )
        self.assertEqual(
            [reference["title"] for reference in second_references],
            ["Authorized document"],
        )
        self.assertEqual(first_diagnostics["runtime_corpus_hit"], 0)
        self.assertEqual(second_diagnostics["runtime_corpus_hit"], 1)
        self.assertEqual(second_diagnostics["corpus_vectors"], 2)
        self.assertEqual(second_diagnostics["corpus_bytes"], 16)
        self.assertEqual(create_embedding.call_count, 2)
        self.assertEqual(generate_answer.call_count, 2)

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._get_client", return_value=Mock())
    @patch("core.utils.generate_gpt_answer", return_value="Cached answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_exact_result_cache_skips_embedding_retrieval_and_answer_generation(
        self,
        create_embedding,
        generate_answer,
        _get_client,
        _cache_scope,
    ):
        first = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )
        second = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )

        self.assertEqual(first[0], "Cached answer")
        self.assertEqual(second[0], "Cached answer")
        self.assertEqual(second[2]["result_cache_hit"], 1)
        self.assertEqual(create_embedding.call_count, 1)
        self.assertEqual(generate_answer.call_count, 1)

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._get_client", return_value=Mock())
    @patch(
        "core.utils.generate_gpt_answer",
        side_effect=["Public answer", "Admin answer"],
    )
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_result_cache_is_partitioned_by_access_scope(
        self,
        _create_embedding,
        generate_answer,
        _get_client,
        _cache_scope,
    ):
        public_result = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )
        admin_result = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="admin-scope",
        )

        self.assertEqual(public_result[0], "Public answer")
        self.assertEqual(admin_result[0], "Admin answer")
        self.assertEqual(generate_answer.call_count, 2)

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._get_client", return_value=Mock())
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_transient_chat_failure_is_not_stored_as_an_exact_result(
        self,
        create_embedding,
        _get_client,
        _cache_scope,
    ):
        def failed_answer(*_args, diagnostics=None, **_kwargs):
            diagnostics["chat_failed"] = 1
            return "Couldn't generate answer right now. Please try again later."

        with patch("core.utils.generate_gpt_answer", side_effect=failed_answer) as generate_answer:
            first = utils.search_pdf_folders(
                [(self.public_folder, None)],
                "member rights",
                result_cache_scope="public-scope",
            )
            second = utils.search_pdf_folders(
                [(self.public_folder, None)],
                "member rights",
                result_cache_scope="public-scope",
            )

        self.assertEqual(first[2]["chat_failed"], 1)
        self.assertEqual(second[2]["result_cache_hit"], 0)
        self.assertEqual(create_embedding.call_count, 2)
        self.assertEqual(generate_answer.call_count, 2)


class QueryEmbeddingCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._test_embeddings_enabled", return_value=False)
    def test_repeated_query_reuses_provider_scoped_embedding(
        self,
        _test_embeddings,
        _cache_scope,
    ):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0])]
        )
        first_diagnostics = {}
        second_diagnostics = {}
        with patch("core.utils._get_client", return_value=client):
            first = utils.create_query_embedding(
                "member rights",
                diagnostics=first_diagnostics,
            )
            second = utils.create_query_embedding(
                "member rights",
                diagnostics=second_diagnostics,
            )

        np.testing.assert_array_equal(first, second)
        client.embeddings.create.assert_called_once()
        self.assertEqual(first_diagnostics["embedding_cache_hit"], 0)
        self.assertEqual(first_diagnostics["embedding_calls"], 1)
        self.assertEqual(second_diagnostics["embedding_cache_hit"], 1)
        self.assertEqual(second_diagnostics["embedding_calls"], 0)

    @patch(
        "core.ai_guard.get_ai_cache_scope",
        side_effect=["provider-scope-a", "provider-scope-b"],
    )
    @patch("core.utils._test_embeddings_enabled", return_value=False)
    def test_embedding_cache_does_not_cross_provider_scopes(
        self,
        _test_embeddings,
        _cache_scope,
    ):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0])]
        )
        with patch("core.utils._get_client", return_value=client):
            utils.create_query_embedding("member rights")
            utils.create_query_embedding("member rights")

        self.assertEqual(client.embeddings.create.call_count, 2)


class RuntimeSearchIdentityTests(SimpleTestCase):
    @override_settings(
        ACTIVE_RUNTIME=SIGNED_RUNTIME,
        RUNTIME_GENERATION_ID="generation-1",
        RUNTIME_MANIFEST_DIGEST=SIGNED_DIGEST,
    )
    def test_exact_signed_runtime_enables_immutable_cache_identity(self):
        self.assertRegex(utils.verified_runtime_search_identity(), r"^[0-9a-f]{64}$")

    @override_settings(
        ACTIVE_RUNTIME=SIGNED_RUNTIME,
        RUNTIME_GENERATION_ID="different-generation",
        RUNTIME_MANIFEST_DIGEST=SIGNED_DIGEST,
    )
    def test_runtime_identity_mismatch_disables_cross_request_cache(self):
        self.assertEqual(utils.verified_runtime_search_identity(), "")
