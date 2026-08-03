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
SCALE_FOLDER_NAME = "Codex Scale Category"
SCOPE_FOLDER_PREFIX = "Codex Scope Category"
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
    Folder.objects.filter(
        name__in=[FOLDER_NAME, RENAMED_FOLDER_NAME, SCALE_FOLDER_NAME]
    ).delete()
    Folder.objects.filter(name__startswith=SCOPE_FOLDER_PREFIX).delete()

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
            and "Data protection" in dashboard.text
            and "Add Category" in dashboard.text
            and ("No categories yet" in dashboard.text or "admin-category-card" in dashboard.text),
            "dashboard UI missing",
        )

        operations = client.get("/dashboard/operations/")
        require(operations.status_code == 200, "backup and restore GET failed")
        for expected in (
            "Data protection",
            "Check health",
            "Back up data",
            "Recovery points",
            "Search maintenance",
            "Activity and technical evidence",
        ):
            require(
                expected in operations.text,
                f"guided data protection workbench missing: {expected}",
            )
        for obsolete in (
            "Refresh data",
            "Storage profiles",
            "Backup &amp; sync jobs",
            "Advanced manual controls",
            'name="source_profile"',
            'name="destination_profile"',
        ):
            require(
                obsolete not in operations.text,
                f"obsolete workbench control found: {obsolete}",
            )
        require(
            "vendor/bootstrap/5.3.0" in operations.text,
            "vendored Bootstrap asset missing",
        )
        require("cdn.jsdelivr.net" not in operations.text, "external Bootstrap dependency found")

        token = csrf_token(operations.text)
        health_check = client.post(
            "/dashboard/data-operations/actions/",
            data={
                "csrfmiddlewaretoken": token,
                "action": "health_check",
            },
        )
        require(health_check.status_code == 200, "read-only health check failed")
        require(
            "Health check" in health_check.text
            and "This check was read-only" in health_check.text,
            "read-only health-check evidence missing",
        )

        maintenance = client.get("/dashboard/data-operations/advanced/")
        require(maintenance.status_code == 200, "maintenance workbench GET failed")
        for expected in (
            "Search maintenance",
            "Local derived data",
            "Maintenance jobs",
        ):
            require(
                expected in maintenance.text,
                f"search maintenance workbench missing: {expected}",
            )
        require(
            'name="action" value="restore"' not in maintenance.text
            and 'name="action" value="backup"' not in maintenance.text,
            "recovery controls leaked into search maintenance",
        )

        state = client.get("/dashboard/data-operations/state/")
        require(state.status_code == 200, "workbench state API failed")
        state_payload = state.json()
        require(
            "posture" in state_payload
            and "v3" in state_payload
            and "permissions" in state_payload
            and "history" in state_payload,
            "v3 workbench state contract missing",
        )
        require(
            "owner_token" not in state.text
            and "secret_key" not in state.text
            and "access_key" not in state.text,
            "workbench state contract or redaction failed",
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
            and "डेटा संरक्षण" in operations_mr.text
            and "डेटाचा बॅकअप घ्या" in operations_mr.text
            and "पुनर्प्राप्ती बिंदू" in operations_mr.text
            and "शोध देखभाल" in operations_mr.text
            and "क्रियाकलाप आणि तांत्रिक पुरावा" in operations_mr.text,
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
        PDFFile.objects.create(
            title="Codex Unavailable PDF",
            folder=folder,
            uploaded_by=operator,
            lifecycle="unavailable",
            indexed=False,
            file="pdfs/codex-unavailable.pdf",
            media_prior_lifecycle="uploaded",
            media_expected_sha256="",
            media_expected_size=None,
            media_quarantine_reason="missing_after_inventory",
            media_case_reference="CI-BROWSER",
        )
        scale_folder = Folder.objects.create(
            name=SCALE_FOLDER_NAME,
            created_by=operator,
        )
        scale_documents = [
            PDFFile(
                title=f"Scale document {number:03d}",
                folder=scale_folder,
                uploaded_by=operator,
                lifecycle="ready",
                indexed=number % 3 != 0,
                file=f"pdfs/scale-document-{number:03d}.pdf",
            )
            for number in range(103)
        ]
        scale_documents.extend(
            [
                PDFFile(
                    title="मराठी सहकारी संस्था दस्तऐवज",
                    folder=scale_folder,
                    uploaded_by=operator,
                    lifecycle="ready",
                    indexed=True,
                    file="pdfs/scale-marathi.pdf",
                ),
                PDFFile(
                    title="VeryLongUnbrokenDocumentTitle" * 8,
                    folder=scale_folder,
                    uploaded_by=None,
                    lifecycle="ready",
                    indexed=False,
                    file="pdfs/scale-long-title.pdf",
                ),
            ]
        )
        PDFFile.objects.bulk_create(scale_documents)
        Folder.objects.bulk_create(
            [
                Folder(
                    name=f"{SCOPE_FOLDER_PREFIX} {number:02d}",
                    created_by=operator,
                )
                for number in range(1, 47)
            ]
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
        "admin http smoke passed: login dashboard data protection workbench "
        "English/Marathi no-JS category keywords rename folder pdf view "
        "users register toggle"
    )


if __name__ == "__main__":
    main()
