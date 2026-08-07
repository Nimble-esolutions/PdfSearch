"""Privacy-bounded product analytics configuration for public search.

The browser adapter is intentionally stage-only and disabled by default.  A
superadmin may enable it after the independently deployed Umami service is
ready; no deployment environment variable or application restart is needed.
"""

from __future__ import annotations

import re

from django.conf import settings
from django.db import DatabaseError, transaction

from .models import SiteSetting


PRODUCT_ANALYTICS_MODE_SETTING = "PRODUCT_ANALYTICS_MODE"
PRODUCT_ANALYTICS_DISABLED = "disabled"
PRODUCT_ANALYTICS_ENABLED = "enabled"
PRODUCT_ANALYTICS_MODES = {
    PRODUCT_ANALYTICS_DISABLED,
    PRODUCT_ANALYTICS_ENABLED,
}

UMAMI_SCRIPT_URL = "https://analytics.ai-sahakar.net/script.js"
UMAMI_WEBSITE_ID = "a947d503-2c2b-4192-8845-877e062efc38"
UMAMI_ALLOWED_HOSTS = ("2026.ai-sahakar.net",)

_SAFE_RELEASE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def get_product_analytics_mode() -> str:
    """Return the persisted mode, failing closed to disabled."""

    try:
        stored = (
            SiteSetting.objects.filter(key=PRODUCT_ANALYTICS_MODE_SETTING)
            .values_list("value", flat=True)
            .first()
        )
    except DatabaseError:
        return PRODUCT_ANALYTICS_DISABLED
    return stored if stored in PRODUCT_ANALYTICS_MODES else PRODUCT_ANALYTICS_DISABLED


@transaction.atomic
def set_product_analytics_mode(mode: str, *, updated_by) -> str:
    """Persist an allowlisted mode for the stage browser integration."""

    candidate = str(mode or "").strip().lower()
    if candidate not in PRODUCT_ANALYTICS_MODES:
        raise ValueError("Unsupported product analytics mode.")
    SiteSetting.objects.update_or_create(
        key=PRODUCT_ANALYTICS_MODE_SETTING,
        defaults={
            "value": candidate,
            "description": "Stage-only, privacy-bounded Umami product analytics",
            "updated_by": updated_by,
        },
    )
    return candidate


def build_public_analytics_config(
    request,
    *,
    search_view: str,
    primary_view: str,
    override_used: bool,
) -> dict | None:
    """Build public browser config only for the exact approved stage host."""

    hostname = request.get_host().partition(":")[0].rstrip(".").lower()
    if hostname not in UMAMI_ALLOWED_HOSTS:
        return None
    if get_product_analytics_mode() != PRODUCT_ANALYTICS_ENABLED:
        return None

    release = str(getattr(settings, "APP_RELEASE_VERSION", "") or "").strip()
    if not _SAFE_RELEASE.fullmatch(release):
        release = ""

    return {
        "script_url": UMAMI_SCRIPT_URL,
        "website_id": UMAMI_WEBSITE_ID,
        "allowed_domains": list(UMAMI_ALLOWED_HOSTS),
        "deployment_tier": "stage",
        "surface": search_view,
        "primary_surface": primary_view,
        "ui_language": "mr" if request.LANGUAGE_CODE.split("-", 1)[0] == "mr" else "en",
        "release_version": release,
        "view_override_used": bool(override_used),
    }
