from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def validate_redis_url(value, *, allow_loopback=False):
    """Validate the cache endpoint before Django starts serving requests."""
    redis_url = (value or "").strip()
    if not redis_url:
        return ""

    parsed = urlparse(redis_url)
    if parsed.scheme.lower() not in {"redis", "rediss"}:
        raise ImproperlyConfigured("REDIS_URL must use redis:// or rediss://")

    try:
        hostname = parsed.hostname
    except ValueError as exc:
        raise ImproperlyConfigured("REDIS_URL contains an invalid hostname") from exc

    if not hostname:
        raise ImproperlyConfigured("REDIS_URL must include a hostname")

    if hostname.lower().rstrip(".") in _LOOPBACK_HOSTS and not allow_loopback:
        raise ImproperlyConfigured(
            "REDIS_URL must not target loopback outside local development; "
            "use the Compose Redis service hostname instead"
        )

    return redis_url
