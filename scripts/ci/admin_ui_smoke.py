#!/usr/bin/env python3
"""Authenticated smoke test for the local Admin UI."""

import os
import re
import sys
from pathlib import Path

import django
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "flowdocs"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.base import ContentFile  # noqa: E402

from core.models import Folder, PDFFile  # noqa: E402


BASE_URL = os.environ.get("ADMIN_SMOKE_BASE_URL", "http://127.0.0.1:8000")
USERNAME = os.environ.get("ADMIN_SMOKE_USERNAME", "codex-admin")
PASSWORD_PATH = Path(os.environ.get("ADMIN_SMOKE_PASSWORD_FILE", "/tmp/codex-admin-password.txt"))
FOLDER_NAME = "Codex Smoke Category"
RENAMED_FOLDER_NAME = "Codex Smoke Category Renamed"
CREATED_USER = "codex-smoke-user"


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def csrf_token(html):
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    require(match is not None, "missing csrf token")
    return match.group(1)


def main():
    require(PASSWORD_PATH.exists(), f"missing password file: {PASSWORD_PATH}")
    password = PASSWORD_PATH.read_text(encoding="utf-8")
    user_model = get_user_model()
    operator = user_model.objects.get(username=USERNAME)
    operator.role = "superadmin"
    operator.is_staff = True
    operator.is_superuser = True
    operator.is_active = True
    operator.save(
        update_fields=[
            "role",
            "is_staff",
            "is_superuser",
            "is_active",
        ]
    )

    user_model.objects.filter(username=CREATED_USER).delete()
    Folder.objects.filter(name__in=[FOLDER_NAME, RENAMED_FOLDER_NAME]).delete()

    with httpx.Client(base_url=BASE_URL, follow_redirects=False, timeout=20.0) as client:
        login_page = client.get("/login/")
        require(login_page.status_code == 200, f"login GET failed: {login_page.status_code}")
        require(
            "admin-shell" in login_page.text and "togglePassword" in login_page.text,
            "login shell missing",
        )

        token = csrf_token(login_page.text)
        login = client.post(
            "/login/",
            data={
                "username": USERNAME,
                "password": password,
                "csrfmiddlewaretoken": token,
                "next": "/dashboard/",
            },
        )
        require(
            login.status_code == 302 and login.headers.get("location") == "/dashboard/",
            f"login POST failed: {login.status_code} {login.headers.get('location')}",
        )

        dashboard = client.get("/dashboard/")
        require(dashboard.status_code == 200, f"dashboard GET failed: {dashboard.status_code}")
        require(
            "Operations Cockpit" in dashboard.text
            and "Needs attention" in dashboard.text
            and "Category Yard" in dashboard.text
            and 'name="readiness"' in dashboard.text
            and 'name="provenance"' in dashboard.text
            and "Active Work" in dashboard.text
            and "Vault posture" in dashboard.text
            and "Add Category" in dashboard.text
            and ("No categories yet" in dashboard.text or "admin-category-card" in dashboard.text),
            "dashboard UI missing",
        )

        operations = client.get("/dashboard/operations/")
        require(
            operations.status_code == 200
            and "Vault & Recovery" in operations.text
            and "Authority comparison" in operations.text
            and "vendor/bootstrap/5.3.0" in operations.text
            and "cdn.jsdelivr.net" not in operations.text,
            "Vault and recovery workbench missing or externally dependent",
        )
        sync = client.get("/dashboard/operations/?section=sync")
        require(
            sync.status_code == 200
            and "Queue publish-only sync" in sync.text
            and 'method="post"' in sync.text,
            "no-JavaScript Active Sync form missing",
        )
        maintenance = client.get("/dashboard/operations/?section=maintenance")
        require(maintenance.status_code == 200, "maintenance workbench GET failed")
        for expected in (
            "Documents",
            "Choose the outcome you need",
            "Unchanged during processing",
            "Preview Repair Stored Indexes",
            'name="filter_indexed"',
        ):
            require(
                expected in maintenance.text,
                f"guided maintenance workbench missing: {expected}",
            )
        state = client.get("/dashboard/operations/api/v1/state/")
        require(state.status_code == 200, "workbench state API failed")
        state_payload = state.json()
        require(
            "state_version" in state_payload
            and "correlation_id" in state_payload
            and "owner_token" not in state.text,
            "workbench state contract or redaction failed",
        )
        retention = client.get("/dashboard/operations/?section=retention")
        require(
            retention.status_code == 200
            and "It does not delete manifests" in retention.text
            and "Create GC dry-run" in retention.text
            and "Permanently delete" not in retention.text,
            "truthful retention and GC controls missing",
        )
        diagnostics = client.get("/dashboard/operations/api/v1/diagnostics/")
        require(
            diagnostics.status_code == 200
            and "credential_alias" not in diagnostics.text
            and "owner_token" not in diagnostics.text
            and "object_key" not in diagnostics.text,
            "diagnostic export contract or redaction failed",
        )
        token = csrf_token(operations.text)
        marathi = client.post(
            "/i18n/setlang/",
            data={
                "csrfmiddlewaretoken": token,
                "language": "mr",
                "next": "/dashboard/operations/",
            },
        )
        require(marathi.status_code == 302, "Marathi switch failed")
        operations_mr = client.get("/dashboard/operations/")
        require(
            operations_mr.status_code == 200
            and "तिजोरी संचालन कार्यपटल" in operations_mr.text,
            "reviewed Marathi workbench language missing",
        )
        token = csrf_token(operations_mr.text)
        english = client.post(
            "/i18n/setlang/",
            data={
                "csrfmiddlewaretoken": token,
                "language": "en",
                "next": "/dashboard/",
            },
        )
        require(english.status_code == 302, "English switch failed")

        token = csrf_token(dashboard.text)
        create = client.post(
            "/folder/create/",
            data={
                "folder_name": FOLDER_NAME,
                "folder_keywords": "smoke, test",
                "csrfmiddlewaretoken": token,
            },
        )
        require(
            create.status_code == 302 and create.headers.get("location") == "/dashboard/",
            f"create folder failed: {create.status_code}",
        )
        folder = Folder.objects.get(name=FOLDER_NAME)

        dashboard = client.get("/dashboard/")
        require(
            dashboard.status_code == 200
            and "admin-category-card" in dashboard.text
            and "Category Yard" in dashboard.text
            and FOLDER_NAME in dashboard.text,
            "created category card missing",
        )

        folder_page = client.get(f"/dashboard/folder/{folder.pk}/")
        require(folder_page.status_code == 200, f"folder page failed: {folder_page.status_code}")
        require(
            "Document Workbench" in folder_page.text
            and "Blast Radius" in folder_page.text
            and "Edit Keywords" in folder_page.text
            and "PDF Title" in folder_page.text,
            "folder UI missing",
        )

        token = csrf_token(folder_page.text)
        update_keywords = client.post(
            f"/folder/{folder.pk}/keywords/",
            data={"keywords": "smoke, reviewed", "csrfmiddlewaretoken": token},
        )
        require(update_keywords.status_code == 302, f"keywords update failed: {update_keywords.status_code}")

        token = csrf_token(client.get("/dashboard/").text)
        rename = client.post(
            f"/folder/{folder.pk}/rename/",
            data={"folder_name": RENAMED_FOLDER_NAME, "csrfmiddlewaretoken": token},
        )
        require(rename.status_code == 302, f"folder rename failed: {rename.status_code}")
        folder.refresh_from_db()
        require(folder.name == RENAMED_FOLDER_NAME, "folder rename did not persist")

        pdf = PDFFile.objects.create(
            title="Codex Smoke PDF",
            folder=folder,
            uploaded_by=user_model.objects.get(username=USERNAME),
        )
        pdf.file.save("codex-smoke.pdf", ContentFile(b"%PDF-1.7\nsmoke"), save=True)

        folder_page = client.get(f"/dashboard/folder/{folder.pk}/")
        require(
            folder_page.status_code == 200 and "Codex Smoke PDF" in folder_page.text,
            "seeded PDF missing from folder UI",
        )
        pdf_view = client.get(f"/pdf/{pdf.pk}/view/")
        require(
            pdf_view.status_code == 200 and pdf_view.headers.get("content-type") == "application/pdf",
            f"pdf view failed: {pdf_view.status_code}",
        )

        users = client.get("/dashboard/users/")
        require(users.status_code == 200, f"user list failed: {users.status_code}")
        require(
            "Users & Access" in users.text
            and "Account directory" in users.text
            and "Current user" in users.text
            and USERNAME in users.text,
            "user list UI missing",
        )

        token = csrf_token(client.get("/register/").text)
        register = client.post(
            "/register/",
            data={
                "username": CREATED_USER,
                "email": "codex-smoke@example.invalid",
                "password1": "CodexSmokePassword123!",
                "password2": "CodexSmokePassword123!",
                "department": "admin",
                "role": "admin",
                "csrfmiddlewaretoken": token,
            },
        )
        require(
            register.status_code == 302 and register.headers.get("location") == "/login/",
            f"register failed: {register.status_code}",
        )
        new_user = user_model.objects.get(username=CREATED_USER)

        users = client.get("/dashboard/users/")
        require(
            users.status_code == 200 and CREATED_USER in users.text and "Deactivate" in users.text,
            "registered user action missing",
        )
        token = csrf_token(users.text)
        toggle = client.post(
            f"/dashboard/users/toggle/{new_user.pk}/",
            data={"csrfmiddlewaretoken": token},
        )
        require(toggle.status_code == 302, f"toggle user failed: {toggle.status_code}")
        new_user.refresh_from_db()
        require(new_user.is_active is False, "toggle user did not persist")

    print(
        "admin http smoke passed: login dashboard vault workbench "
        "English/Marathi no-JS category keywords rename folder pdf view "
        "users register toggle"
    )


if __name__ == "__main__":
    main()
