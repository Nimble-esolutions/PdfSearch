"""Safe, typed metadata for the settings and configuration center."""

from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class ConfigurationDefinition:
    key: str
    label: str
    group: str
    editable: bool = False
    restart_required: bool = True
    secret: bool = False


CONFIGURATION_DEFINITIONS = (
    ConfigurationDefinition("PUBLIC_SEARCH_ENABLED", "Public search", "Search policy", True, False),
    ConfigurationDefinition("PUBLIC_SEARCH_ALL_FOLDERS", "All folders searchable", "Search policy"),
    ConfigurationDefinition("PUBLIC_SEARCH_MAX_WORDS", "Maximum query words", "Search policy"),
    ConfigurationDefinition("PUBLIC_SEARCH_RATE_LIMIT", "Requests per window", "Search policy"),
    ConfigurationDefinition("PUBLIC_SEARCH_RATE_WINDOW", "Rate window seconds", "Search policy"),
    ConfigurationDefinition("OPENAI_EMBED_MODEL", "Embedding model", "Search runtime"),
    ConfigurationDefinition("OPENAI_CHAT_MODEL", "Chat model", "Search runtime"),
    ConfigurationDefinition("EXTERNAL_AI_MODE", "AI provider mode", "Search runtime", True, False),
    ConfigurationDefinition("DISPLAY_SERVICE_FOOTER", "Service footer", "Runtime controls", True, False),
    ConfigurationDefinition("MAINTENANCE_SCHEDULER_ENABLED", "Maintenance scheduler", "Runtime controls", True, False),
    ConfigurationDefinition("BACKUP_SYNC_MODE", "Backup sync mode", "Runtime controls", True, False),
    ConfigurationDefinition("DATA_MODE", "Data mode", "Runtime controls", True, False),
    ConfigurationDefinition("EXTERNAL_SIDE_EFFECTS_MODE", "External side effects", "Runtime controls", True, False),
    ConfigurationDefinition("SETTINGS_EDIT_ENABLED", "Settings editing", "Runtime controls"),
    ConfigurationDefinition("REDIS_URL", "Redis backend", "Infrastructure", secret=True),
    ConfigurationDefinition("MAX_FILE_SIZE_MB", "Maximum upload size (MB)", "Document processing"),
    ConfigurationDefinition(
        "ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES",
        "Maximum existing-media custody size (bytes)",
        "Document processing",
    ),
    ConfigurationDefinition("PDF_CHUNK_SIZE", "PDF chunk size", "Document processing"),
    ConfigurationDefinition("PDF_CHUNK_OVERLAP", "PDF chunk overlap", "Document processing"),
    ConfigurationDefinition("PDF_OCR_FALLBACK_ENABLED", "PDF OCR fallback", "Document processing"),
    ConfigurationDefinition("PDF_OCR_BINARY", "PDF OCR executable", "Document processing"),
    ConfigurationDefinition("PDF_OCR_LANGUAGES", "PDF OCR languages", "Document processing"),
    ConfigurationDefinition("PDF_OCR_DPI", "PDF OCR resolution", "Document processing"),
    ConfigurationDefinition("PDF_OCR_MAX_PAGES", "PDF OCR page cap", "Document processing"),
    ConfigurationDefinition("PDF_OCR_PAGE_TIMEOUT_SECONDS", "PDF OCR page timeout", "Document processing"),
    ConfigurationDefinition("PDF_OCR_MAX_SECONDS", "PDF OCR document timeout", "Document processing"),
    ConfigurationDefinition("PDF_OCR_MAX_PIXELS", "PDF OCR pixel cap", "Document processing"),
    ConfigurationDefinition("MAX_CONTEXT_WORDS", "Maximum context words", "Document processing"),
    ConfigurationDefinition("TOP_K_CHUNKS", "Retrieved chunks", "Document processing"),
    ConfigurationDefinition("EMBEDDING_TTL", "Embedding cache TTL", "Document processing"),
    ConfigurationDefinition("SEARCH_CACHE_TTL", "Search cache TTL", "Document processing"),
    ConfigurationDefinition("DEBUG", "Debug mode", "Security posture"),
    ConfigurationDefinition("ALLOWED_HOSTS", "Allowed hosts", "Security posture"),
    ConfigurationDefinition("CSRF_COOKIE_SECURE", "Secure CSRF cookie", "Security posture"),
    ConfigurationDefinition("SESSION_COOKIE_SECURE", "Secure session cookie", "Security posture"),
    ConfigurationDefinition("SECURE_SSL_REDIRECT", "SSL redirect", "Security posture"),
    ConfigurationDefinition("SECURE_HSTS_SECONDS", "HSTS seconds", "Security posture"),
    ConfigurationDefinition("EMAIL_HOST", "Email host", "Notifications"),
    ConfigurationDefinition("EMAIL_PORT", "Email port", "Notifications"),
    ConfigurationDefinition("EMAIL_USE_TLS", "Email TLS", "Notifications"),
    ConfigurationDefinition("EMAIL_HOST_USER", "Email account", "Notifications"),
    ConfigurationDefinition("SECRET_KEY", "Django secret key", "Secrets", secret=True),
    ConfigurationDefinition("OPENAI_API_KEY", "OpenAI API key", "Secrets", secret=True),
    ConfigurationDefinition("EMAIL_HOST_PASSWORD", "Email password", "Secrets", secret=True),
)


def _display_value(definition: ConfigurationDefinition, value: Any) -> str:
    if definition.secret:
        return "Configured" if value else "Not configured"
    if value is None or value == "":
        return "Not configured"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or "Not configured"
    if isinstance(value, bool):
        return "Enabled" if value else "Disabled"
    return str(value)


def build_configuration_groups(settings_obj, db_values=None):
    """Resolve configuration into safe rows grouped for the admin UI.

    This reports effective posture without returning secret values. Runtime
    flags may come from SiteSetting; deployment settings remain read-only.
    """
    db_values = db_values or {}
    groups = []
    grouped = {}
    for definition in CONFIGURATION_DEFINITIONS:
        db_value = db_values.get(definition.key)
        if definition.editable and db_value not in (None, ""):
            raw_value, source = db_value, "database"
        elif definition.key in os.environ:
            raw_value, source = os.environ.get(definition.key), "environment"
        else:
            raw_value, source = getattr(settings_obj, definition.key, ""), "default"
        row = {
            "key": definition.key,
            "label": definition.label,
            "value": _display_value(definition, raw_value),
            "source": source,
            "editable": definition.editable,
            "restart_required": definition.restart_required,
            "secret": definition.secret,
        }
        grouped.setdefault(definition.group, []).append(row)
    for name, rows in grouped.items():
        groups.append({"name": name, "rows": rows})
    return groups
