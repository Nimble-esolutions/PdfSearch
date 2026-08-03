"""OpenAI side-effect containment tests.

Verifies ai_guard correctly blocks/sandboxes/allows external AI calls.
Uses unittest.mock to control policy since ENV_IDENTITY is frozen at startup.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-key")
os.environ.setdefault("DATA_BOOTSTRAP_MODE", "empty")

import django
django.setup()


class OpenAIContainmentTests(unittest.TestCase):
    def test_disabled_mode_blocks_openai_client(self):
        with patch("core.ai_guard._ai_policy_mode", return_value="disabled"):
            from core.ai_guard import get_openai_client, ExternalAIBlocked
            with self.assertRaises(ExternalAIBlocked):
                get_openai_client()

    def test_sandbox_mode_returns_fake_client(self):
        with patch("core.ai_guard._ai_policy_mode", return_value="sandbox"):
            from core.ai_guard import get_openai_client
            client = get_openai_client()
            resp = client.chat.completions.create(
                messages=[{"role": "user", "content": "hello"}]
            )
            self.assertIn("SANDBOX", resp.choices[0].message.content)

    def test_sandbox_embeddings_are_deterministic(self):
        with patch("core.ai_guard._ai_policy_mode", return_value="sandbox"):
            from core.ai_guard import get_embedding_provider
            provider = get_embedding_provider()
            r1 = provider(["hello world"])
            r2 = provider(["hello world"])
            self.assertEqual(len(r1), 1)
            self.assertEqual(len(r1[0]), 1536)
            self.assertEqual(r1, r2, "Fake embeddings must be deterministic")

    def test_sandbox_chat_is_deterministic(self):
        with patch("core.ai_guard._ai_policy_mode", return_value="sandbox"):
            from core.ai_guard import get_chat_provider
            provider = get_chat_provider()
            r1 = provider([{"role": "user", "content": "hello"}])
            r2 = provider([{"role": "user", "content": "hello"}])
            self.assertIn("SANDBOX", r1)
            self.assertEqual(r1, r2)

    def test_enabled_without_key_fails_closed(self):
        with patch("core.ai_guard._ai_policy_mode", return_value="enabled"):
            os.environ.pop("OPENAI_API_KEY", None)
            from core.ai_guard import get_openai_client, ExternalAIBlocked
            with self.assertRaises(ExternalAIBlocked):
                get_openai_client()

    def test_explicit_ai_mode_overrides_sandbox_side_effects(self):
        """Stage may enable AI without enabling unrelated external effects."""
        from django.conf import settings
        from core.ai_guard import get_chat_provider

        with patch.object(settings, "EXTERNAL_AI_MODE", "enabled", create=True), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "stage-test-key"}):
            with patch("openai.OpenAI") as openai_client:
                openai_client.return_value.chat.completions.create.return_value.choices = [
                    type("Choice", (), {"message": type("Message", (), {"content": "real stage answer"})()})()
                ]
                provider = get_chat_provider()
                self.assertEqual(provider([{"role": "user", "content": "hello"}]), "real stage answer")
                openai_client.assert_called_once_with(api_key="stage-test-key")

    def test_invalid_explicit_ai_mode_falls_back_to_side_effect_policy(self):
        from django.conf import settings
        from core.ai_guard import _ai_policy_mode

        with patch.object(settings, "EXTERNAL_AI_MODE", "not-a-mode", create=True):
            self.assertEqual(_ai_policy_mode(), "disabled")

    def test_sandbox_is_forbidden_in_reachable_environments(self):
        from django.conf import settings
        from core.ai_guard import ExternalAIBlocked, get_openai_client
        from core.environment import AppEnv

        for app_env in (AppEnv.REVIEW, AppEnv.STAGING, AppEnv.PRODUCTION):
            with self.subTest(app_env=app_env.value), \
                 patch.object(
                     settings,
                     "ENV_IDENTITY",
                     SimpleNamespace(app_env=app_env),
                 ), \
                 patch("core.ai_guard._ai_policy_mode", return_value="sandbox"), \
                 patch.dict(os.environ, {"CI": "", "PDFSEARCH_TEST_EMBEDDINGS": ""}):
                with self.assertRaisesRegex(ExternalAIBlocked, "restricted"):
                    get_openai_client()

    def test_explicit_ci_lifecycle_may_use_deterministic_provider(self):
        from django.conf import settings
        from core.ai_guard import get_openai_client
        from core.environment import AppEnv

        with patch.object(
            settings,
            "ENV_IDENTITY",
            SimpleNamespace(app_env=AppEnv.STAGING),
        ), \
             patch("core.ai_guard._ai_policy_mode", return_value="sandbox"), \
             patch.dict(os.environ, {"CI": "true", "PDFSEARCH_TEST_EMBEDDINGS": "1"}):
            self.assertIn("Fake", type(get_openai_client()).__name__)

    def test_test_embeddings_are_forbidden_on_ordinary_stage(self):
        from django.conf import settings
        from core.ai_guard import ExternalAIBlocked
        from core.environment import AppEnv
        from core.utils import _test_embeddings_enabled

        with patch.object(
            settings,
            "ENV_IDENTITY",
            SimpleNamespace(app_env=AppEnv.STAGING),
        ), \
             patch.dict(os.environ, {"CI": "", "PDFSEARCH_TEST_EMBEDDINGS": "1"}):
            with self.assertRaises(ExternalAIBlocked):
                _test_embeddings_enabled()

    def test_answer_cache_is_not_read_before_provider_validation(self):
        from core.utils import SearchDataIntegrityError, generate_gpt_answer

        with patch("core.utils._get_client", side_effect=SearchDataIntegrityError("blocked")), \
             patch("core.utils.cache.get") as cache_get:
            with self.assertRaises(SearchDataIntegrityError):
                generate_gpt_answer("question", "context")
            cache_get.assert_not_called()

    def test_answer_cache_uses_provider_scoped_digest(self):
        from core.utils import generate_gpt_answer

        with patch("core.utils._get_client", return_value=object()), \
             patch("core.ai_guard.get_ai_cache_scope", return_value="test:scope"), \
             patch("core.utils.cache.get", return_value="cached answer") as cache_get:
            self.assertEqual(
                generate_gpt_answer("question", "context"),
                "cached answer",
            )
            cache_key = cache_get.call_args.args[0]
            self.assertRegex(cache_key, r"^gpt_ans:v2:[0-9a-f]{64}$")
