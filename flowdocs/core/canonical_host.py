"""Canonical host policy for the future 2026 production application.

The legacy www service remains outside this application until an explicitly
approved production cutover. Once the 2026 app receives both production
aliases, this middleware makes the apex host the only host that can establish
application sessions or analytics identities.
"""

from django.http import HttpResponsePermanentRedirect


PRODUCTION_CANONICAL_HOST = "ai-sahakar.net"
PRODUCTION_ALIAS_HOSTS = ("www.ai-sahakar.net",)


def _hostname(request) -> str:
    return request.get_host().partition(":")[0].rstrip(".").lower()


def canonical_production_alias_redirect(request):
    """Redirect an exact production alias to the one canonical HTTPS host."""

    if _hostname(request) not in PRODUCTION_ALIAS_HOSTS:
        return None
    return HttpResponsePermanentRedirect(
        f"https://{PRODUCTION_CANONICAL_HOST}{request.get_full_path()}"
    )


class CanonicalProductionHostMiddleware:
    """Canonicalize production aliases before security, session, or app code."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect_response = canonical_production_alias_redirect(request)
        if redirect_response is not None:
            return redirect_response
        return self.get_response(request)
