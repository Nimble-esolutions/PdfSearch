"""OpenAI policy guard with fake/sandbox/test providers.

All OpenAI calls must go through one of these entry points:
  get_openai_client() → returns client or raises DisabledError
  get_embedding_provider() → returns callable for embeddings
  get_chat_provider() → returns callable for chat completions

Respects the AI-specific policy first, falling back to the broader external
side-effect policy for backwards compatibility:
  enabled  → real OpenAI client
  sandbox  → deterministic fake provider
  disabled → raises ExternalAIBlocked

``EXTERNAL_AI_MODE`` deliberately stays separate from
``EXTERNAL_SIDE_EFFECTS_MODE``.  A stage instance can use real AI answers while
keeping email, webhooks, payments, and other side effects sandboxed.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from django.conf import settings


class ExternalAIBlocked(RuntimeError):
    """Raised when an external AI call is blocked by policy."""


class FakeAIProviderUsed(Exception):
    """Raised in test mode to assert no real AI call was made."""


def _ai_policy_mode() -> str:
    explicit_mode = str(getattr(settings, "EXTERNAL_AI_MODE", "") or "").strip().lower()
    if explicit_mode in {"enabled", "sandbox", "disabled"}:
        return explicit_mode
    identity = getattr(settings, "ENV_IDENTITY", None)
    if identity is None:
        return "disabled"
    return identity.external_side_effects.value


def get_openai_client():
    """Return an OpenAI client or raise ExternalAIBlocked."""
    mode = _ai_policy_mode()
    if mode == "disabled":
        raise ExternalAIBlocked("OpenAI calls disabled by side-effect policy")
    if mode == "sandbox":
        return _FakeOpenAIClient()
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ExternalAIBlocked("OpenAI API key not configured")
    return OpenAI(api_key=api_key)


def get_embedding_provider() -> Callable:
    """Return function(texts) -> embeddings based on policy."""
    mode = _ai_policy_mode()
    if mode == "disabled":
        raise ExternalAIBlocked("Embedding generation disabled by side-effect policy")
    if mode == "sandbox":
        return _fake_embeddings
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ExternalAIBlocked("OpenAI API key not configured for embeddings")
    client = OpenAI(api_key=api_key)
    model = getattr(settings, "OPENAI_EMBED_MODEL", "text-embedding-3-small")

    def _real_embeddings(texts):
        resp = client.embeddings.create(input=texts, model=model)
        return [d.embedding for d in resp.data]
    return _real_embeddings


def get_chat_provider() -> Callable:
    """Return function(messages, **kwargs) -> answer based on policy."""
    mode = _ai_policy_mode()
    if mode == "disabled":
        raise ExternalAIBlocked("Chat generation disabled by side-effect policy")
    if mode == "sandbox":
        return _fake_chat
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ExternalAIBlocked("OpenAI API key not configured for chat")
    client = OpenAI(api_key=api_key)
    model = getattr(settings, "OPENAI_CHAT_MODEL", "gpt-4o-mini")

    def _real_chat(messages, **kwargs):
        resp = client.chat.completions.create(
            model=model, messages=messages, **kwargs,
        )
        return resp.choices[0].message.content
    return _real_chat


def _fake_embeddings(texts: list[str]) -> list[list[float]]:
    dims = getattr(settings, "OPENAI_EMBED_DIMENSIONS", 1536) or 1536
    result = []
    for i, t in enumerate(texts):
        seed = hashlib.sha256(f"fake-embed:{i}:{t[:50]}".encode()).digest()
        vec = [((seed[j % len(seed)] / 255.0) - 0.5) * 2.0 for j in range(dims)]
        result.append(vec)
    return result


def _fake_chat(messages: list[dict], **kwargs) -> str:
    return "[SANDBOX] This is a deterministic sandbox response for testing purposes."


class _FakeOpenAIClient:
    """A fake OpenAI client that never makes network requests."""

    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                messages = kwargs.get("messages", [])
                return _FakeChatResponse(
                    "[SANDBOX] Fake chat response."
                )

    class embeddings:
        @staticmethod
        def create(**kwargs):
            texts = kwargs.get("input", [])
            if isinstance(texts, str):
                texts = [texts]
            data = [
                _FakeEmbeddingData(_fake_embeddings([t])[0], i)
                for i, t in enumerate(texts)
            ]
            return _FakeEmbeddingResponse(data)

    def __getattr__(self, name):
        return self


class _FakeChatResponse:
    def __init__(self, content):
        self.choices = [self]
        self.message = self

    @property
    def content(self):
        return "[SANDBOX] Deterministic fake chat response."


class _FakeEmbeddingResponse:
    def __init__(self, data):
        self.data = data


class _FakeEmbeddingData:
    def __init__(self, embedding, index):
        self.embedding = embedding
        self.index = index
