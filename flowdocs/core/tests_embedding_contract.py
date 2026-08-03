import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.embedding_contract import (
    EmbeddingContractError,
    embedding_dimension_for_model,
    observed_embedding_dimensions,
    require_incremental_embedding_compatibility,
)
from core.management.commands import prepare_dataops_candidate as candidate_command
from core.utils import _deterministic_embeddings


class EmbeddingContractTests(SimpleTestCase):
    def test_known_openai_models_use_provider_default_dimensions(self):
        self.assertEqual(embedding_dimension_for_model("text-embedding-3-small"), 1536)
        self.assertEqual(embedding_dimension_for_model("text-embedding-3-large"), 3072)
        self.assertEqual(embedding_dimension_for_model("text-embedding-ada-002"), 1536)

    def test_unknown_model_is_rejected_instead_of_guessing(self):
        with self.assertRaisesMessage(
            EmbeddingContractError,
            "embedding_model_dimension_unknown",
        ):
            embedding_dimension_for_model("unregistered-model")

    def test_incremental_repair_rejects_mixed_or_changed_dimensions(self):
        with self.assertRaisesMessage(
            EmbeddingContractError,
            "embedding_model_dimension_mismatch",
        ):
            require_incremental_embedding_compatibility(
                model="text-embedding-3-small",
                observed_dimensions={2, 1536},
            )
        with self.assertRaisesMessage(
            EmbeddingContractError,
            "embedding_model_dimension_mismatch",
        ):
            require_incremental_embedding_compatibility(
                model="text-embedding-3-large",
                observed_dimensions={1536},
            )

    def test_observation_ignores_empty_and_malformed_entries(self):
        self.assertEqual(
            observed_embedding_dimensions(
                [None, [], [[1.0, 0.0]], ["invalid"], [[1.0] * 1536]]
            ),
            {2, 1536},
        )

    @patch("core.utils.OPENAI_EMBED_MODEL", "text-embedding-3-small")
    def test_deterministic_embeddings_match_configured_model_dimension(self):
        vectors = _deterministic_embeddings(["one", "two"])
        self.assertEqual([len(vector) for vector in vectors], [1536, 1536])
        self.assertIsNot(vectors[0], vectors[1])


class CandidateEmbeddingPreflightTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.digest = "a" * 64
        for name, payload in (
            (
                ".dataops-restore.json",
                {"verified": True, "manifest_sha256": self.digest},
            ),
            (
                ".dataops-rehearsal.json",
                {"success": True, "manifest_sha256": self.digest},
            ),
        ):
            (self.workspace / name).write_text(json.dumps(payload))

    def tearDown(self):
        self.temporary.cleanup()

    @override_settings(
        OPENAI_EMBED_MODEL="text-embedding-3-large",
        EXTERNAL_EMBEDDINGS_ENABLED=True,
    )
    @patch.dict(os.environ, {"MAINTENANCE_CANDIDATE_EXECUTION": "1"})
    @patch.object(candidate_command, "_quarantine_legacy_indexes")
    @patch.object(candidate_command, "classify_search_artifacts")
    @patch.object(candidate_command.PDFFile, "objects")
    def test_model_change_is_rejected_before_candidate_mutation(
        self,
        objects,
        classify,
        quarantine,
    ):
        pdf = SimpleNamespace(
            file=None,
            lifecycle="ready",
            indexed=True,
            page_chunks=["existing"],
            chunk_embeddings=[[0.0] * 1536],
        )
        queryset = MagicMock()
        queryset.iterator.return_value = iter([pdf])
        objects.filter.return_value.order_by.return_value = queryset
        classify.return_value = SimpleNamespace(
            blocking=False,
            reindex_required=False,
        )

        with override_settings(DATA_ROOT=str(self.workspace)):
            with self.assertRaisesMessage(
                CommandError,
                "candidate_embedding_model_dimension_mismatch",
            ):
                candidate_command.Command().handle(
                    manifest_digest=self.digest,
                    maximum_external_documents=500,
                )

        quarantine.assert_not_called()
