from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

import numpy as np
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.query import QuerySet
from django.test import TestCase, override_settings

from core import utils
from core.models import Folder, PDFFile
from vaultops.models import SourceMutationState
from vaultops.services.mutations import get_mutation_state


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
    VAULT_MUTATION_TRACKING_ENABLED=True,
)
class SignedSearchPerformanceTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.faiss_directory = TemporaryDirectory()
        self.addCleanup(self.faiss_directory.cleanup)
        faiss_settings = override_settings(
            FAISS_INDEX_DIR=self.faiss_directory.name,
        )
        faiss_settings.enable()
        self.addCleanup(faiss_settings.disable)
        cache.clear()
        utils._runtime_search_corpus = None
        utils._runtime_search_corpus_disabled_identity = ""
        self.mutation_state = get_mutation_state()
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
        self.authorized_pdf = PDFFile.objects.create(
            title="Authorized document",
            file="pdfs/authorized.pdf",
            folder=self.public_folder,
            uploaded_by=self.user,
            page_chunks=["authorized source text"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        self.hidden_pdf = PDFFile.objects.create(
            title="Hidden document",
            file="pdfs/hidden.pdf",
            folder=self.hidden_folder,
            uploaded_by=self.user,
            page_chunks=["hidden source text"],
            chunk_embeddings=[[0.0, 1.0]],
        )
        if utils._HAS_FAISS:
            utils.build_or_load_faiss_index_for_folder(
                self.public_folder,
                force_rebuild=True,
            )
            utils.build_or_load_faiss_index_for_folder(
                self.hidden_folder,
                force_rebuild=True,
            )
            self.mutation_state.refresh_from_db()

    def tearDown(self):
        utils._runtime_search_corpus = None
        utils._runtime_search_corpus_disabled_identity = ""
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
        self.assertEqual(
            second_diagnostics["corpus_bytes"],
            16
            + len("authorized source text".encode())
            + len("hidden source text".encode()),
        )
        self.assertEqual(create_embedding.call_count, 2)
        self.assertEqual(generate_answer.call_count, 2)

    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_corpus_build_preallocates_without_vector_vstack(self, _create_embedding):
        with (
            patch("core.utils.np.vstack", side_effect=AssertionError("vstack used")),
            patch("core.utils.generate_gpt_answer", return_value="Authorized answer"),
        ):
            answer, references, diagnostics = utils.search_pdf_folders(
                [(self.public_folder, None)],
                "member rights",
            )

        self.assertEqual(answer, "Authorized answer")
        self.assertEqual(
            [item["pdf_id"] for item in references],
            [self.authorized_pdf.pk],
        )
        self.assertEqual(diagnostics["corpus_vectors"], 2)

    def test_corpus_build_fetches_one_json_row_at_a_time(self):
        original_iterator = QuerySet.iterator
        chunk_sizes = []

        def recording_iterator(queryset, *args, **kwargs):
            chunk_sizes.append(kwargs.get("chunk_size"))
            yield from original_iterator(queryset, *args, **kwargs)

        with patch.object(QuerySet, "iterator", new=recording_iterator):
            utils._load_runtime_search_corpus(
                utils.verified_runtime_search_identity(),
            )

        self.assertEqual(chunk_sizes, [1, 1])

    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_mutation_during_answer_generation_rejects_stale_evidence(
        self,
        _create_embedding,
    ):
        def mutate_before_answer(*_args, **_kwargs):
            PDFFile.objects.filter(pk=self.authorized_pdf.pk).update(
                lifecycle="archived"
            )
            SourceMutationState.objects.using("control").filter(
                pk=self.mutation_state.pk
            ).update(current_epoch=self.mutation_state.current_epoch + 1)
            return "Must not be returned"

        with patch(
            "core.utils.generate_gpt_answer",
            side_effect=mutate_before_answer,
        ):
            with self.assertRaisesRegex(
                utils.SearchDataIntegrityError,
                "Search source changed during request",
            ):
                utils.search_pdf_folders(
                    [(self.public_folder, None)],
                    "member rights",
                )

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._get_client", return_value=Mock())
    @patch("core.utils.generate_gpt_answer", return_value="Fresh answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_mutation_epoch_reloads_corpus_and_prevents_stale_reference(
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
        self.assertEqual(
            [item["pdf_id"] for item in first[1]],
            [self.authorized_pdf.pk],
        )

        self.authorized_pdf.lifecycle = "archived"
        self.authorized_pdf.save(update_fields=["lifecycle"])
        SourceMutationState.objects.using("control").filter(
            pk=self.mutation_state.pk
        ).update(current_epoch=self.mutation_state.current_epoch + 1)

        second = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )

        self.assertEqual(second[0], "")
        self.assertEqual(second[1], [])
        self.assertEqual(second[2]["result_cache_hit"], 0)
        self.assertEqual(create_embedding.call_count, 2)
        generate_answer.assert_called_once()

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._get_client", return_value=Mock())
    @patch("core.utils.generate_gpt_answer", return_value="Fresh answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_cached_reference_visibility_fails_back_when_epoch_was_not_advanced(
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
        self.assertEqual(
            [item["pdf_id"] for item in first[1]],
            [self.authorized_pdf.pk],
        )

        PDFFile.objects.filter(pk=self.authorized_pdf.pk).update(lifecycle="archived")
        second = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )

        self.assertEqual(second[0], "")
        self.assertEqual(second[1], [])
        self.assertEqual(second[2]["result_cache_hit"], 0)
        self.assertEqual(create_embedding.call_count, 2)
        generate_answer.assert_called_once()

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._get_client", return_value=Mock())
    @patch("core.utils.generate_gpt_answer", return_value="Fresh answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_untracked_title_change_cannot_return_stale_cached_reference(
        self,
        create_embedding,
        generate_answer,
        _get_client,
        _cache_scope,
    ):
        utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )
        PDFFile.objects.filter(pk=self.authorized_pdf.pk).update(
            title="Renamed authorized document"
        )

        second = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )

        self.assertEqual(second[2]["result_cache_hit"], 0)
        self.assertEqual(second[1][0]["title"], "Renamed authorized document")
        self.assertEqual(create_embedding.call_count, 2)
        self.assertEqual(generate_answer.call_count, 2)

    @patch("core.utils.generate_gpt_answer", return_value="Authorized answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_oversized_signed_corpus_uses_bounded_fallback(
        self,
        _create_embedding,
        _generate_answer,
    ):
        with patch.object(utils, "MAX_RUNTIME_CORPUS_VECTORS", 1):
            answer, references, diagnostics = utils.search_pdf_folders(
                [(self.public_folder, None)],
                "member rights",
            )

        self.assertEqual(answer, "Authorized answer")
        self.assertEqual(
            [item["pdf_id"] for item in references],
            [self.authorized_pdf.pk],
        )
        self.assertEqual(diagnostics["runtime_corpus_hit"], 0)
        self.assertTrue(utils._runtime_search_corpus_disabled_identity)
        if utils._HAS_FAISS:
            index_path = Path(
                utils.faiss_index_path_for_folder(self.public_folder)
            )
            self.assertEqual(index_path.parent, Path(self.faiss_directory.name))
            self.assertTrue(index_path.is_file())

    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_bounded_fallback_still_rejects_mid_answer_mutation(
        self,
        _create_embedding,
    ):
        def mutate_before_answer(*_args, **_kwargs):
            SourceMutationState.objects.using("control").filter(
                pk=self.mutation_state.pk
            ).update(current_epoch=self.mutation_state.current_epoch + 1)
            return "Must not be returned"

        with (
            patch.object(utils, "MAX_RUNTIME_CORPUS_VECTORS", 1),
            patch(
                "core.utils.generate_gpt_answer",
                side_effect=mutate_before_answer,
            ),
        ):
            with self.assertRaisesRegex(
                utils.SearchDataIntegrityError,
                "Search source changed during request",
            ):
                utils.search_pdf_folders(
                    [(self.public_folder, None)],
                    "member rights",
                )

    @patch("core.utils.generate_gpt_answer", return_value="Authorized answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_corrupt_signed_corpus_never_serves_a_partial_snapshot(
        self,
        _create_embedding,
        _generate_answer,
    ):
        self.hidden_pdf.page_chunks = ["first", "second"]
        self.hidden_pdf.save(update_fields=["page_chunks"])

        answer, references, diagnostics = utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
        )

        self.assertEqual(answer, "Authorized answer")
        self.assertEqual(
            [item["pdf_id"] for item in references],
            [self.authorized_pdf.pk],
        )
        self.assertEqual(diagnostics["corpus_vectors"], 0)
        self.assertTrue(utils._runtime_search_corpus_disabled_identity)

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._get_client", return_value=Mock())
    @patch("core.utils.generate_gpt_answer", return_value="Authorized answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_result_cache_outage_does_not_block_search(
        self,
        _create_embedding,
        _generate_answer,
        _get_client,
        _cache_scope,
    ):
        with (
            patch("core.utils.cache.get", side_effect=RuntimeError("cache unavailable")),
            patch("core.utils.cache.set", side_effect=RuntimeError("cache unavailable")),
        ):
            answer, references, diagnostics = utils.search_pdf_folders(
                [(self.public_folder, None)],
                "member rights",
                result_cache_scope="public-scope",
            )

        self.assertEqual(answer, "Authorized answer")
        self.assertEqual(
            [item["pdf_id"] for item in references],
            [self.authorized_pdf.pk],
        )
        self.assertEqual(diagnostics["cache_errors"], 2)

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
    @patch("core.utils.generate_gpt_answer", return_value="Cached answer")
    @patch(
        "core.utils.create_query_embedding",
        return_value=np.array([1.0, 0.0], dtype=np.float32),
    )
    def test_exact_result_hit_does_not_warm_a_new_worker_corpus(
        self,
        create_embedding,
        generate_answer,
        _get_client,
        _cache_scope,
    ):
        utils.search_pdf_folders(
            [(self.public_folder, None)],
            "member rights",
            result_cache_scope="public-scope",
        )
        utils._runtime_search_corpus = None

        with patch("core.utils._load_runtime_search_corpus") as load_corpus:
            second = utils.search_pdf_folders(
                [(self.public_folder, None)],
                "member rights",
                result_cache_scope="public-scope",
            )

        self.assertEqual(second[0], "Cached answer")
        self.assertEqual(second[2]["result_cache_hit"], 1)
        load_corpus.assert_not_called()
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

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._test_embeddings_enabled", return_value=False)
    def test_cache_outage_does_not_block_provider_embedding(
        self,
        _test_embeddings,
        _cache_scope,
    ):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0])]
        )
        diagnostics = {}
        with (
            patch("core.utils._get_client", return_value=client),
            patch("core.utils.cache.get", side_effect=RuntimeError("cache unavailable")),
            patch("core.utils.cache.set", side_effect=RuntimeError("cache unavailable")),
        ):
            embedding = utils.create_query_embedding(
                "member rights",
                diagnostics=diagnostics,
            )

        np.testing.assert_array_equal(
            embedding,
            np.array([1.0, 0.0], dtype=np.float32),
        )
        client.embeddings.create.assert_called_once()
        self.assertEqual(diagnostics["cache_errors"], 2)

    @patch("core.ai_guard.get_ai_cache_scope", return_value="provider-scope")
    @patch("core.utils._test_embeddings_enabled", return_value=False)
    def test_query_embedding_cache_uses_bounded_ttl(
        self,
        _test_embeddings,
        _cache_scope,
    ):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0])]
        )
        with (
            patch("core.utils._get_client", return_value=client),
            patch("core.utils.cache.get", return_value=None),
            patch("core.utils.cache.set") as cache_set,
        ):
            utils.create_query_embedding("member rights")

        self.assertEqual(
            cache_set.call_args.args[2],
            utils.QUERY_EMBEDDING_CACHE_TTL,
        )
        self.assertLessEqual(utils.QUERY_EMBEDDING_CACHE_TTL, utils.SEARCH_CACHE_TTL)


class FaissSearchContractTests(TestCase):
    @skipUnless(utils._HAS_FAISS, "FAISS is not installed")
    def test_l2_index_is_rejected_for_inner_product_ranking(self):
        index = utils.faiss.IndexFlatL2(2)
        index.add(np.array([[1.0, 0.0]], dtype=np.float32))

        with self.assertRaisesRegex(
            utils.SearchDataIntegrityError,
            "metric is not inner product",
        ):
            utils._validate_index(index, chunk_count=1, dimensions=2)

    @skipUnless(utils._HAS_FAISS, "FAISS is not installed")
    def test_same_shape_reordered_vectors_fail_exact_index_validation(self):
        expected = np.array(
            [[1.0, 0.0], [0.0, 1.0]],
            dtype=np.float32,
        )
        index = utils.faiss.IndexFlatIP(2)
        index.add(expected[::-1].copy())

        self.assertFalse(utils._index_matches_embeddings(index, expected))


@override_settings(VAULT_MUTATION_TRACKING_ENABLED=True)
class RuntimeSearchIdentityTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        get_mutation_state()

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

    @override_settings(
        ACTIVE_RUNTIME=SIGNED_RUNTIME,
        RUNTIME_GENERATION_ID="generation-1",
        RUNTIME_MANIFEST_DIGEST=SIGNED_DIGEST,
        VAULT_MUTATION_TRACKING_ENABLED=False,
    )
    def test_disabled_mutation_tracking_uses_conservative_search_path(self):
        self.assertEqual(utils.verified_runtime_search_identity(), "")

    @override_settings(
        ACTIVE_RUNTIME=SIGNED_RUNTIME,
        RUNTIME_GENERATION_ID="generation-1",
        RUNTIME_MANIFEST_DIGEST=SIGNED_DIGEST,
    )
    def test_active_mutation_disables_cross_request_cache(self):
        state = get_mutation_state()
        state.active_mutations = 1
        state.save(update_fields=["active_mutations"])
        self.assertEqual(utils.verified_runtime_search_identity(), "")
