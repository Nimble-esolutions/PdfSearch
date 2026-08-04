"""Canonical destinations for authored operator guidance.

Stable reason-code sections predate the simplified Data Operations v3 pages.
Keep their meaning, but resolve them to the current UI so guidance never lands
on a compatibility redirect that discards the requested task.
"""

from urllib.parse import urlencode

from django.urls import reverse


_SECTION_DESTINATIONS = {
    "overview": ("dataops:workbench", "dataops-status-heading"),
    "restore": ("dataops:workbench", "dataops-recovery-points-heading"),
    "activation": ("dataops:workbench", "dataops-recovery-points-heading"),
    "generations": ("dataops:workbench", "dataops-recovery-points-heading"),
    "retention": ("dataops:workbench", "dataops-recovery-points-heading"),
    "maintenance": ("dataops:advanced", "dataops-search-maintenance-heading"),
    "jobs": ("dataops:jobs", ""),
    "sync": ("dataops:jobs", ""),
    "configuration": ("dataops:configuration", ""),
}


def operator_section_url(section, *, plan="", job="", profile=""):
    """Return the current task destination for a stable operator section."""
    route_name, fragment = _SECTION_DESTINATIONS.get(
        str(section or "").strip().lower(),
        ("dataops:workbench", "dataops-status-heading"),
    )
    query = {}
    if route_name == "dataops:advanced":
        if plan:
            query["plan"] = str(plan)
        if job:
            query["job"] = str(job)
    elif route_name == "dataops:configuration" and profile:
        query["profile"] = str(profile)

    url = reverse(route_name)
    if query:
        url = f"{url}?{urlencode(query)}"
    if fragment:
        url = f"{url}#{fragment}"
    return url
