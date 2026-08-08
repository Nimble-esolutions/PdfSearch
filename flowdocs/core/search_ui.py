"""Public-search presentation selection.

The Classic and Workbench frontends intentionally have separate templates,
stylesheets, and JavaScript. This module is the narrow server-side boundary
that selects one presentation without changing the search API.
"""

from dataclasses import dataclass
import os

from django.core.cache import cache
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models import SiteSetting


PRIMARY_SEARCH_VIEW_SETTING = "PUBLIC_SEARCH_PRIMARY_VIEW"
DEFAULT_SEARCH_VIEW = "classic"


@dataclass(frozen=True)
class SearchViewDefinition:
    value: str
    label: str
    description: str
    template_name: str


SEARCH_VIEW_DEFINITIONS = (
    SearchViewDefinition(
        value="classic",
        label=_("Classic search"),
        description=_("Original AI Sahakar public search layout."),
        template_name="search_classic.html",
    ),
    SearchViewDefinition(
        value="workbench",
        label=_("Knowledge workbench"),
        description=_(
            "Evidence-first research layout with persistent topic and source panels."
        ),
        template_name="search.html",
    ),
)
SEARCH_VIEWS = {definition.value: definition for definition in SEARCH_VIEW_DEFINITIONS}
_CACHE_KEY = f"sitesetting:{PRIMARY_SEARCH_VIEW_SETTING}"


def normalize_search_view(value, *, fallback=DEFAULT_SEARCH_VIEW):
    """Return an allowlisted view name and fail closed to ``fallback``."""

    candidate = str(value or "").strip().lower()
    return candidate if candidate in SEARCH_VIEWS else fallback


def supported_search_view(value):
    """Return a normalized allowlisted view, or ``None`` for invalid input."""

    candidate = str(value or "").strip().lower()
    return candidate if candidate in SEARCH_VIEWS else None


def get_primary_search_view():
    """Resolve the persisted primary view, defaulting safely to Classic."""

    if PRIMARY_SEARCH_VIEW_SETTING in os.environ:
        return normalize_search_view(os.environ[PRIMARY_SEARCH_VIEW_SETTING])

    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return normalize_search_view(cached)
    stored = (
        SiteSetting.objects.filter(key=PRIMARY_SEARCH_VIEW_SETTING)
        .values_list("value", flat=True)
        .first()
    )
    resolved = normalize_search_view(stored)
    cache.set(_CACHE_KEY, resolved, timeout=300)
    return resolved


def resolve_search_view(requested_view):
    """Apply a valid non-persistent URL override or use the primary view."""

    candidate = supported_search_view(requested_view)
    if candidate:
        return candidate
    return get_primary_search_view()


def search_template_for(view_name):
    return SEARCH_VIEWS[normalize_search_view(view_name)].template_name


@transaction.atomic
def set_primary_search_view(view_name, *, updated_by):
    """Persist an allowlisted primary view for this application dataset."""

    if PRIMARY_SEARCH_VIEW_SETTING in os.environ:
        raise ValueError("Public search presentation is controlled by the deployment environment.")

    candidate = str(view_name or "").strip().lower()
    if candidate not in SEARCH_VIEWS:
        raise ValueError("Unsupported public search view.")
    SiteSetting.objects.update_or_create(
        key=PRIMARY_SEARCH_VIEW_SETTING,
        defaults={
            "value": candidate,
            "description": "Primary public search presentation",
            "updated_by": updated_by,
        },
    )
    cache.delete(_CACHE_KEY)
    return candidate
