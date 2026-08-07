"""Consent-led, pseudonymous Umami configuration for public search.

The application owns the visitor-consent and identity lifecycle.  Umami only
receives a versioned HMAC alias, never the browser cookie, a PdfSearch account
identifier, a query, or a document reference.  Local hosts are deliberately
not profiles and therefore cannot collect analytics.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core import signing
from django.db import DatabaseError, transaction

from .models import SiteSetting


PRODUCT_ANALYTICS_MODE_SETTING = "PRODUCT_ANALYTICS_MODE"
PRODUCT_ANALYTICS_WEBSITE_ID_SETTING = "PRODUCT_ANALYTICS_WEBSITE_ID"
PRODUCT_ANALYTICS_DISABLED = "disabled"
PRODUCT_ANALYTICS_ENABLED = "enabled"
PRODUCT_ANALYTICS_MODES = {
    PRODUCT_ANALYTICS_DISABLED,
    PRODUCT_ANALYTICS_ENABLED,
}

UMAMI_SCRIPT_URL = "https://analytics.ai-sahakar.net/script.js"
STAGE_UMAMI_WEBSITE_ID = "a947d503-2c2b-4192-8845-877e062efc38"
# Backwards-compatible export for integrations and historic tests.  It is the
# stage fallback only and must never be used for production collection.
UMAMI_WEBSITE_ID = STAGE_UMAMI_WEBSITE_ID

ANALYTICS_CONSENT_COOKIE = "__Host-sahakar-analytics-consent"
ANALYTICS_IDENTITY_COOKIE = "__Host-sahakar-analytics-id"
ANALYTICS_CONSENT_MAX_AGE_SECONDS = 180 * 24 * 60 * 60
ANALYTICS_IDENTITY_MAX_AGE_SECONDS = 90 * 24 * 60 * 60
ANALYTICS_IDENTITY_VERSION = 1
CONSENT_GRANTED = "granted"
CONSENT_DECLINED = "declined"
CONSENT_UNSET = "unset"

_CONSENT_SALT = "core.product-analytics.consent.v1"
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
_SAFE_RELEASE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class AnalyticsProfile:
    hostname: str
    deployment_tier: str
    default_website_id: str = ""


# Do not add localhost, preview, legacy www, or wildcard domains here.  The
# profile map is the hard collection boundary; a running app on any other host
# cannot emit a configuration or issue analytics identity cookies.
ANALYTICS_PROFILES = {
    "2026.ai-sahakar.net": AnalyticsProfile(
        hostname="2026.ai-sahakar.net",
        deployment_tier="stage",
        default_website_id=STAGE_UMAMI_WEBSITE_ID,
    ),
    "ai-sahakar.net": AnalyticsProfile(
        hostname="ai-sahakar.net",
        deployment_tier="production",
    ),
}
UMAMI_ALLOWED_HOSTS = tuple(ANALYTICS_PROFILES)


def _hostname(request) -> str:
    return request.get_host().partition(":")[0].rstrip(".").lower()


def analytics_profile_for_request(request) -> AnalyticsProfile | None:
    """Return an exact collection profile; all other hosts fail closed."""

    return ANALYTICS_PROFILES.get(_hostname(request))


def _setting_key(base_key: str, profile: AnalyticsProfile) -> str:
    """Scope mutable analytics configuration to one exact deployment host."""

    return f"{base_key}:{profile.hostname}"


def _site_setting_value(key: str) -> str:
    try:
        return (
            SiteSetting.objects.filter(key=key).values_list("value", flat=True).first()
            or ""
        )
    except DatabaseError:
        return ""


def get_product_analytics_mode(profile: AnalyticsProfile | None) -> str:
    """Return one host's persisted collection mode, failing closed to disabled.

    The previous stage-only implementation stored the mode under the unscoped
    ``PRODUCT_ANALYTICS_MODE`` key.  It remains a read-only compatibility
    fallback for the stage host only; production never reads it, so a copied
    control database cannot inherit stage collection unexpectedly.
    """

    if profile is None:
        return PRODUCT_ANALYTICS_DISABLED
    stored = _site_setting_value(_setting_key(PRODUCT_ANALYTICS_MODE_SETTING, profile))
    if not stored and profile.hostname == "2026.ai-sahakar.net":
        stored = _site_setting_value(PRODUCT_ANALYTICS_MODE_SETTING)
    return stored if stored in PRODUCT_ANALYTICS_MODES else PRODUCT_ANALYTICS_DISABLED


def _valid_website_id(value: str | None) -> str:
    """Accept only a public Umami UUID; it is configuration, never a secret."""

    candidate = str(value or "").strip()
    try:
        parsed = uuid.UUID(candidate)
    except (AttributeError, TypeError, ValueError):
        return ""
    # Keep the server contract aligned with the browser adapter.  UUID(nil) and
    # unversioned UUIDs are technically parseable but are not valid Umami site
    # identifiers, and accepting either makes a broken configuration look live.
    if not parsed.int or parsed.version not in range(1, 9):
        return ""
    return str(parsed)


def get_product_analytics_website_id(profile: AnalyticsProfile | None) -> str:
    """Resolve this deployment's Umami website without cross-environment reuse."""

    if profile is None:
        return ""
    # Deliberately do not read an unscoped Website ID.  A stale stage value
    # must never become a production tenant identifier after a data restore.
    stored = _site_setting_value(_setting_key(PRODUCT_ANALYTICS_WEBSITE_ID_SETTING, profile))
    return _valid_website_id(stored) or profile.default_website_id


def product_analytics_website_id_source(profile: AnalyticsProfile | None) -> str:
    """Describe the safe source of a host's public tracker identifier.

    The value is intentionally a small presentation enum rather than the raw
    setting key.  It lets Settings distinguish a saved host value from the
    deliberate stage fallback without exposing implementation details to a
    public browser payload.
    """

    if profile is None:
        return "unavailable"
    stored = _valid_website_id(
        _site_setting_value(_setting_key(PRODUCT_ANALYTICS_WEBSITE_ID_SETTING, profile))
    )
    if stored:
        return "saved_host_setting"
    if profile.default_website_id:
        return "built_in_stage_default"
    return "missing"


def product_analytics_status(request) -> dict[str, object]:
    """Provide a secret-free operator status for the host handling this request."""

    profile = analytics_profile_for_request(request)
    website_id = get_product_analytics_website_id(profile)
    mode = get_product_analytics_mode(profile)
    return {
        "approved_host": bool(profile),
        "hostname": _hostname(request),
        "deployment_tier": profile.deployment_tier if profile else "local_or_unapproved",
        "mode": mode,
        "website_id": website_id,
        "website_id_configured": bool(website_id),
        "website_id_source": product_analytics_website_id_source(profile),
        "collection_available": bool(
            profile
            and website_id
            and mode == PRODUCT_ANALYTICS_ENABLED
        ),
    }


@transaction.atomic
def set_product_analytics_mode(
    mode: str,
    *,
    updated_by,
    profile: AnalyticsProfile | None,
    website_id: str | None = None,
) -> str:
    """Persist the current deployment's collection posture and public website ID."""

    if profile is None:
        raise ValueError("Product analytics are unavailable on this host.")

    candidate = str(mode or "").strip().lower()
    if candidate not in PRODUCT_ANALYTICS_MODES:
        raise ValueError("Unsupported product analytics mode.")

    submitted_website_id = str(website_id or "").strip()
    configured_website_id = _valid_website_id(submitted_website_id)
    if submitted_website_id and not configured_website_id:
        raise ValueError("Umami website ID must be a valid UUID.")
    if configured_website_id:
        SiteSetting.objects.update_or_create(
            key=_setting_key(PRODUCT_ANALYTICS_WEBSITE_ID_SETTING, profile),
            defaults={
                "value": configured_website_id,
                "description": "Public Umami website identifier for this deployment host",
                "updated_by": updated_by,
            },
        )

    effective_website_id = configured_website_id or get_product_analytics_website_id(profile)
    if candidate == PRODUCT_ANALYTICS_ENABLED and not effective_website_id:
        raise ValueError("Configure this host's Umami website ID before enabling collection.")

    SiteSetting.objects.update_or_create(
        key=_setting_key(PRODUCT_ANALYTICS_MODE_SETTING, profile),
        defaults={
            "value": candidate,
            "description": "Consent-led, pseudonymous Umami collection for this approved host",
            "updated_by": updated_by,
        },
    )
    return candidate


def _signed_consent_value(state: str) -> str:
    return signing.dumps(
        {"state": state, "version": ANALYTICS_IDENTITY_VERSION},
        salt=_CONSENT_SALT,
        compress=True,
    )


def consent_state(request) -> str:
    """Read the signed consent preference without trusting client input."""

    raw = request.COOKIES.get(ANALYTICS_CONSENT_COOKIE, "")
    if not raw:
        return CONSENT_UNSET
    try:
        payload = signing.loads(
            raw,
            salt=_CONSENT_SALT,
            max_age=ANALYTICS_CONSENT_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        return CONSENT_UNSET
    state = payload.get("state") if isinstance(payload, dict) else ""
    return state if state in {CONSENT_GRANTED, CONSENT_DECLINED} else CONSENT_UNSET


def global_privacy_control_enabled(request) -> bool:
    """Honor the browser's server-visible Global Privacy Control signal."""

    return str(request.META.get("HTTP_SEC_GPC", "")).strip() == "1"


def _identity_token(request) -> str:
    raw = request.COOKIES.get(ANALYTICS_IDENTITY_COOKIE, "")
    return raw if _IDENTITY_PATTERN.fullmatch(raw) else ""


def _new_identity_token() -> str:
    # 256 bits of browser-held randomness, deliberately unrelated to accounts.
    return secrets.token_urlsafe(32)


def _identity_alias(*, token: str, profile: AnalyticsProfile) -> str:
    """Derive a short irreversible Umami distinct ID from the HttpOnly token."""

    secret = str(settings.SECRET_KEY).encode("utf-8")
    material = f"analytics:{ANALYTICS_IDENTITY_VERSION}:{profile.hostname}:{token}".encode("utf-8")
    digest = hmac.new(secret, material, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    # Umami accepts short distinct IDs; keep room for its 50-character limit.
    return f"v{ANALYTICS_IDENTITY_VERSION}_{encoded[:43]}"


def _identity_for_request(request, profile: AnalyticsProfile) -> tuple[str, str | None]:
    """Return an alias and an optional fresh cookie token for a consented visitor."""

    token = _identity_token(request)
    if token:
        return _identity_alias(token=token, profile=profile), None
    token = _new_identity_token()
    return _identity_alias(token=token, profile=profile), token


def set_analytics_cookie(response, name: str, value: str, *, max_age: int) -> None:
    """Set a host-only, HTTPS-only preference or identity cookie."""

    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="Lax",
    )


def set_consent_cookie(response, state: str) -> None:
    if state not in {CONSENT_GRANTED, CONSENT_DECLINED}:
        raise ValueError("Unsupported analytics consent state.")
    set_analytics_cookie(
        response,
        ANALYTICS_CONSENT_COOKIE,
        _signed_consent_value(state),
        max_age=ANALYTICS_CONSENT_MAX_AGE_SECONDS,
    )


def set_identity_cookie(response, token: str) -> None:
    if not _IDENTITY_PATTERN.fullmatch(token):
        raise ValueError("Invalid analytics identity token.")
    set_analytics_cookie(
        response,
        ANALYTICS_IDENTITY_COOKIE,
        token,
        max_age=ANALYTICS_IDENTITY_MAX_AGE_SECONDS,
    )


def rotate_identity_cookie(response) -> None:
    """Replace the opaque browser token without exposing it to JavaScript."""

    set_identity_cookie(response, _new_identity_token())


def clear_identity_cookie(response) -> None:
    response.delete_cookie(
        ANALYTICS_IDENTITY_COOKIE,
        path="/",
        samesite="Lax",
    )


def apply_pending_identity_cookie(response, request) -> None:
    """Persist a rotation created while rendering a previously consented page."""

    if getattr(request, "_clear_analytics_identity_cookie", False):
        clear_identity_cookie(response)
        return
    token = getattr(request, "_pending_analytics_identity_token", None)
    if token:
        set_identity_cookie(response, token)


def build_public_analytics_config(
    request,
    *,
    search_view: str,
    primary_view: str,
    override_used: bool,
) -> dict | None:
    """Build a minimal, host-scoped browser contract after explicit consent."""

    profile = analytics_profile_for_request(request)
    if profile is None or get_product_analytics_mode(profile) != PRODUCT_ANALYTICS_ENABLED:
        return None
    website_id = get_product_analytics_website_id(profile)
    if not website_id:
        return None

    state = consent_state(request)
    identity_alias = ""
    identity_ready = False
    gpc_blocked = global_privacy_control_enabled(request)
    if state == CONSENT_GRANTED and not gpc_blocked:
        identity_alias, pending_token = _identity_for_request(request, profile)
        if pending_token:
            request._pending_analytics_identity_token = pending_token
        identity_ready = bool(identity_alias)
    elif gpc_blocked:
        # A former opt-in must not leave a dormant persistent ID behind when a
        # browser later asserts GPC. The consent record remains so the visitor
        # can make an informed choice after disabling GPC.
        request._clear_analytics_identity_cookie = True

    release = str(getattr(settings, "APP_RELEASE_VERSION", "") or "").strip()
    if not _SAFE_RELEASE.fullmatch(release):
        release = ""

    return {
        "script_url": UMAMI_SCRIPT_URL,
        "website_id": website_id,
        "allowed_domains": [profile.hostname],
        "deployment_tier": profile.deployment_tier,
        "surface": search_view,
        "primary_surface": primary_view,
        "ui_language": "mr" if request.LANGUAGE_CODE.split("-", 1)[0] == "mr" else "en",
        "release_version": release,
        "view_override_used": bool(override_used),
        "consent_status": state,
        "identity_alias": identity_alias,
        "identity_ready": identity_ready,
        "dnt_policy": "explicit_consent_overrides_dnt",
        "gpc_policy": "always_block",
        "gpc_blocked": gpc_blocked,
    }
