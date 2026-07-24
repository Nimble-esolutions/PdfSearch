"""Exercise the running Gunicorn process with disposable, in-container fixtures."""

from __future__ import annotations

import json
import re
import sys
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener


sys.path.insert(0, "/app/flowdocs")

import django
from django.core.files.base import ContentFile


BASE_URL = "http://127.0.0.1:8000"
USERNAME = "ci-admin"
PASSWORD = "ci-only-password-not-for-production"
FOLDER_NAME = "CI Runtime Smoke"
PDF_TITLE = "CI Representative Search Fixture"
PDF_BYTES = b"%PDF-1.7\nCI disposable protected PDF fixture\n"
SEARCH_QUERY = "\u0928\u093f\u092f\u092e 42"
SEARCH_CONTEXT = "\u0928\u093f\u092f\u092e 42: disposable CI search fixture"


class NoRedirectHandler(HTTPRedirectHandler):
    handler_order = 0

    def redirect_request(self, request, file, code, msg, headers, newurl):
        return None


def request(opener, path, method="GET", data=None, headers=None):
    request_headers = {"User-Agent": "pdfsearch-ci-smoke/1", **(headers or {})}
    body = urlencode(data).encode() if data is not None else None
    request_obj = Request(BASE_URL + path, data=body, headers=request_headers, method=method)
    try:
        with opener.open(request_obj, timeout=15) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers), error.read()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def csrf_token(body):
    match = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', body)
    require(match is not None, "CSRF token missing from rendered page")
    return match.group(1).decode()


def create_fixtures():
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
    django.setup()
    from django.contrib.auth import get_user_model

    from core.models import Folder, PDFFile

    user, _ = get_user_model().objects.get_or_create(username=USERNAME)
    user.role = "admin"
    user.is_active = True
    user.set_password(PASSWORD)
    user.save()

    folder, _ = Folder.objects.get_or_create(
        name=FOLDER_NAME,
        defaults={"created_by": user, "keywords": ["\u0928\u093f\u092f\u092e"]},
    )
    folder.created_by = user
    folder.keywords = ["\u0928\u093f\u092f\u092e"]
    folder.save(update_fields=["created_by", "keywords"])
    PDFFile.objects.filter(folder=folder).delete()

    pdf = PDFFile(
        title=PDF_TITLE,
        uploaded_by=user,
        folder=folder,
        extracted_text=SEARCH_CONTEXT,
        page_chunks=[SEARCH_CONTEXT],
        chunk_embeddings=[[1.0, 0.0]],
        text_content=SEARCH_CONTEXT,
        indexed=True,
    )
    pdf.file.save("ci-runtime-fixture.pdf", ContentFile(PDF_BYTES), save=False)
    pdf.save()
    print(f"[fixture] created disposable folder={folder.pk} pdf={pdf.pk}")


def main():
    create_fixtures()
    unauthenticated = build_opener(NoRedirectHandler())
    authenticated = build_opener(NoRedirectHandler(), HTTPCookieProcessor(CookieJar()))

    status, _, body = request(unauthenticated, "/livez")
    require(status == 200 and json.loads(body)["status"] == "ok", "livez failed")

    status, _, body = request(unauthenticated, "/readyz")
    ready = json.loads(body)
    require(status in (200, 503), f"readyz HTTP {status}: {ready}")
    require(isinstance(ready.get("checks"), dict), f"readyz missing checks: {ready}")
    require(
        all(ready["checks"].get(k) == v for k, v in
            {"database": "ok", "cache": "ok", "migrations": "ok"}.items()),
        f"unexpected readiness checks: {ready}",
    )
    # Verify all expected readiness sub-checks are present
    for subcheck in ("data", "backup"):
        assert subcheck in ready["checks"], f"/readyz missing sub-check: {subcheck}"

    status, _, body = request(unauthenticated, "/")
    require(status == 200 and b"AI Enabled Search" in body, "search landing page failed")
    status, _, body = request(unauthenticated, "/static/main/css/style.css")
    require(status == 200 and b".searchBG" in body, "static asset failed")

    # /health/data/ — data generation status
    status, _, body = request(unauthenticated, "/health/data/")
    require(status == 200, f"/health/data/ returned {status}")
    data_health = json.loads(body)
    require("status" in data_health, "/health/data/ missing 'status' key")

    # /health/lease/ — writer lease status
    status, _, body = request(unauthenticated, "/health/lease/")
    require(status == 200, f"/health/lease/ returned {status}")
    lease_health = json.loads(body)
    require("status" in lease_health, "/health/lease/ missing 'status' key")

    # /health/metrics/ — Prometheus metrics
    status, _, body = request(unauthenticated, "/health/metrics/")
    require(status == 200, f"/health/metrics/ returned {status}")
    require(b"pdfsearch" in body, "/health/metrics/ missing pdfsearch metrics")

    status, headers, _ = request(unauthenticated, "/dashboard/")
    require(status == 302 and headers.get("Location", "").startswith("/login/"), "dashboard permission gate failed")

    status, _, login_page = request(authenticated, "/login/")
    require(status == 200, "login page failed")
    token = csrf_token(login_page)
    status, headers, _ = request(
        authenticated,
        "/login/",
        method="POST",
        data={"username": USERNAME, "password": PASSWORD, "csrfmiddlewaretoken": token},
        headers={"Referer": BASE_URL + "/login/"},
    )
    require(status == 302 and headers.get("Location") == "/dashboard/", "login failed")

    status, _, dashboard = request(authenticated, "/dashboard/")
    require(status == 200 and b"CI Runtime Smoke" in dashboard and b"/dashboard/folder/" in dashboard, "dashboard fixture route failed")
    folder_match = re.search(rb"/dashboard/folder/(\d+)/", dashboard)
    require(folder_match is not None, "folder navigation link missing")
    folder_path = f"/dashboard/folder/{folder_match.group(1).decode()}/"

    status, _, folder_page = request(authenticated, folder_path)
    require(status == 200 and PDF_TITLE.encode() in folder_page, "folder navigation failed")
    pdf_match = re.search(rb"/pdf/(\d+)/view/", folder_page)
    require(pdf_match is not None, "protected PDF link missing")
    pdf_path = f"/pdf/{pdf_match.group(1).decode()}/view/"

    status, headers, _ = request(unauthenticated, pdf_path)
    require(status == 302 and headers.get("Location", "").startswith("/login/"), "PDF permission gate failed")
    status, headers, body = request(authenticated, pdf_path)
    require(status == 200 and headers.get("Content-Type") == "application/pdf" and body == PDF_BYTES, "protected PDF response failed")

    status, _, search_page = request(authenticated, "/search/")
    require(status == 200, "search page failed")
    token = csrf_token(search_page)
    status, _, body = request(
        authenticated,
        "/search/",
        method="POST",
        data={"query": SEARCH_QUERY, "csrfmiddlewaretoken": token},
        headers={"X-CSRFToken": token, "Referer": BASE_URL + "/search/"},
    )
    result = json.loads(body)
    require(status == 200 and any(ref.get("title") == PDF_TITLE for ref in result.get("references", [])), f"representative search failed: {result}")
    print("[runtime] livez readiness migrations permissions dashboard folder PDF static search: passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[runtime] FAILED: {error}", file=sys.stderr)
        raise
