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


@dataclass(frozen=True)
class RuntimeSettingDefinition:
    key: str
    label: str
    group: str
    description: str
    input_type: str = "text"
    choices: tuple[tuple[str, str], ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    high_impact: bool = False


RUNTIME_SETTING_DEFINITIONS = (
    RuntimeSettingDefinition(
        "PUBLIC_SEARCH_ENABLED", "Public search", "Public search", "Allow visitors to search published documents.", "select",
        (("1", "Enabled"), ("0", "Disabled")), high_impact=True,
    ),
    RuntimeSettingDefinition(
        "DISPLAY_SERVICE_FOOTER", "Service footer", "Public search", "Show the service attribution and policy links on public search.", "select",
        (("1", "Visible"), ("0", "Hidden")),
    ),
    RuntimeSettingDefinition(
        "PUBLIC_SEARCH_MAX_WORDS", "Maximum question words", "Search limits", "Reject questions above this word count before expensive search work begins.", "number",
        minimum=1, maximum=100,
    ),
    RuntimeSettingDefinition(
        "PUBLIC_SEARCH_RATE_LIMIT", "Requests per window", "Search limits", "Maximum public search requests allowed per visitor in the configured window.", "number",
        minimum=1, maximum=600,
    ),
    RuntimeSettingDefinition(
        "PUBLIC_SEARCH_RATE_WINDOW", "Rate-limit window (seconds)", "Search limits", "Length of the rolling public-search rate-limit window.", "number",
        minimum=10, maximum=86_400,
    ),
)


CONFIGURATION_DEFINITIONS = (
    ConfigurationDefinition("PUBLIC_SEARCH_ENABLED", "Public search", "Search policy", True, False),
    ConfigurationDefinition("PUBLIC_SEARCH_ALL_FOLDERS", "All folders searchable", "Search policy"),
    ConfigurationDefinition("PUBLIC_SEARCH_MAX_WORDS", "Maximum query words", "Search policy", True, False),
    ConfigurationDefinition("PUBLIC_SEARCH_RATE_LIMIT", "Requests per window", "Search policy", True, False),
    ConfigurationDefinition("PUBLIC_SEARCH_RATE_WINDOW", "Rate window seconds", "Search policy", True, False),
    ConfigurationDefinition("OPENAI_EMBED_MODEL", "Embedding model", "Search runtime"),
    ConfigurationDefinition("OPENAI_CHAT_MODEL", "Chat model", "Search runtime"),
    ConfigurationDefinition("EXTERNAL_AI_MODE", "AI provider mode", "Search runtime"),
    ConfigurationDefinition("DISPLAY_SERVICE_FOOTER", "Service footer", "Runtime controls", True, False),
    ConfigurationDefinition("MAINTENANCE_SCHEDULER_ENABLED", "Maintenance scheduler", "Runtime controls"),
    ConfigurationDefinition("BACKUP_SYNC_MODE", "Backup sync mode", "Runtime controls"),
    ConfigurationDefinition("DATA_MODE", "Data mode", "Runtime controls"),
    ConfigurationDefinition("EXTERNAL_SIDE_EFFECTS_MODE", "External side effects", "Runtime controls"),
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


def build_runtime_setting_groups(settings_obj, db_values=None):
    """Build editable controls for settings that the request path reads live."""
    db_values = db_values or {}
    grouped = {}
    for definition in RUNTIME_SETTING_DEFINITIONS:
        db_value = db_values.get(definition.key)
        if db_value not in (None, ""):
            current, source = db_value, "database override"
        elif definition.key in os.environ:
            current, source = os.environ[definition.key], "environment"
        else:
            current, source = getattr(settings_obj, definition.key, ""), "application default"
        if isinstance(current, bool):
            current = "1" if current else "0"
        row = {
            "key": definition.key,
            "label": definition.label,
            "description": definition.description,
            "current": str(current),
            "source": source,
            "input_type": definition.input_type,
            "choices": definition.choices,
            "minimum": definition.minimum,
            "maximum": definition.maximum,
            "high_impact": definition.high_impact,
        }
        grouped.setdefault(definition.group, []).append(row)
    return [{"name": name, "rows": rows} for name, rows in grouped.items()]
