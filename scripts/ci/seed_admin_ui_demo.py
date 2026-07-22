#!/usr/bin/env python3
"""Seed local Admin UI demo data with multilingual generated PDF fixtures."""

import os
import sys
from io import BytesIO
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "flowdocs"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
django.setup()

import fitz  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.base import ContentFile  # noqa: E402

from core.models import Folder, PDFFile  # noqa: E402


CATEGORY_PREFIX = "Demo Category"
PDF_PREFIX = "Demo PDF"
DEFAULT_COUNT = 20
PAGE_RECT = fitz.Rect(0, 0, 595, 842)
BODY_RECT = fitz.Rect(72, 148, 523, 750)
DEVANAGARI_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.otf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/mangal/Mangal.ttf",
)


FIXTURES = [
    {
        "language": "English",
        "mode": "unicode-text",
        "category": "Loan Operations",
        "title": "Cooperative Loan Intake Notice",
        "body": (
            "Member applications received this week require KYC verification, "
            "loan committee review, and branch-level sanction tracking.\n\n"
            "Priority queue: dairy members, women-led self-help groups, and "
            "renewal cases due before the month-end closing cycle."
        ),
    },
    {
        "language": "English",
        "mode": "unicode-text",
        "category": "Audit Compliance",
        "title": "Internal Audit Closure Memo",
        "body": (
            "Open audit observations must be tagged by risk owner, evidence "
            "status, and expected closure date. Material findings need board "
            "visibility before the next statutory review."
        ),
    },
    {
        "language": "English",
        "mode": "unicode-text",
        "category": "Procurement",
        "title": "Vendor Renewal Checklist",
        "body": (
            "Renewal files should include purchase orders, comparative quotes, "
            "delivery confirmation, tax registration, and conflict-of-interest "
            "declarations from approving officers."
        ),
    },
    {
        "language": "English",
        "mode": "unicode-text",
        "category": "Board Records",
        "title": "Board Resolution Extract",
        "body": (
            "The board approved digitization of member service records, subject "
            "to privacy controls, access logs, and quarterly review of document "
            "retention exceptions."
        ),
    },
    {
        "language": "English",
        "mode": "unicode-text",
        "category": "Training",
        "title": "Branch Training Attendance",
        "body": (
            "Training coverage is measured by attendance, assessment score, "
            "and follow-up completion. Branch managers must upload evidence "
            "within seven working days."
        ),
    },
    {
        "language": "English",
        "mode": "unicode-text",
        "category": "Recovery",
        "title": "Recovery Case Summary",
        "body": (
            "Recovery follow-up notes should separate hardship cases from "
            "wilful default, record notices sent, and link payment plans to "
            "approved member communication."
        ),
    },
    {
        "language": "English",
        "mode": "photo-ocr",
        "category": "Field Inspection",
        "title": "Field Visit Scan",
        "body": (
            "Scanned field visit record for crop loan verification. The image "
            "contains handwritten-style notes, site condition remarks, and "
            "branch officer initials for OCR pipeline testing."
        ),
    },
    {
        "language": "English",
        "mode": "photo-ocr",
        "category": "Deposit Desk",
        "title": "Deposit Slip Scan",
        "body": (
            "Image-only deposit desk fixture for testing scan intake, OCR "
            "transcript review, and unknown-owner warnings in the Admin UI."
        ),
    },
    {
        "language": "Hindi",
        "mode": "unicode-text",
        "category": "ऋण संचालन",
        "title": "सहकारी ऋण स्वीकृति सूचना",
        "body": (
            "सदस्य के ऋण आवेदन में पहचान सत्यापन, आय विवरण और समिति की "
            "स्वीकृति दर्ज की गई है। शाखा अधिकारी को वितरण से पहले सभी "
            "दस्तावेजों की जांच करनी है।"
        ),
    },
    {
        "language": "Hindi",
        "mode": "unicode-text",
        "category": "लेखा परीक्षण",
        "title": "आंतरिक लेखा परीक्षण टिप्पणी",
        "body": (
            "खुले निरीक्षण बिंदुओं को जोखिम स्तर, जिम्मेदार अधिकारी और "
            "समापन तिथि के साथ चिह्नित किया गया है। उच्च जोखिम मामलों को "
            "अगली बोर्ड बैठक में रखना आवश्यक है।"
        ),
    },
    {
        "language": "Hindi",
        "mode": "unicode-text",
        "category": "सदस्य सेवा",
        "title": "सदस्य शिकायत निवारण रिपोर्ट",
        "body": (
            "शिकायत पंजी में प्राप्त आवेदन, समाधान की स्थिति और सदस्य को "
            "भेजी गई सूचना दर्ज है। लंबित मामलों की समीक्षा प्रत्येक सोमवार "
            "को की जाएगी।"
        ),
    },
    {
        "language": "Hindi",
        "mode": "unicode-text",
        "category": "प्रशिक्षण",
        "title": "शाखा प्रशिक्षण उपस्थिति",
        "body": (
            "कर्मचारियों ने डिजिटल दस्तावेज प्रबंधन, खोज तैयारी और गोपनीयता "
            "नियमों पर प्रशिक्षण पूरा किया। उपस्थिति सूची और मूल्यांकन "
            "अंक संलग्न हैं।"
        ),
    },
    {
        "language": "Hindi",
        "mode": "photo-ocr",
        "category": "स्कैन अभिलेख",
        "title": "फसल ऋण निरीक्षण स्कैन",
        "body": (
            "फसल ऋण निरीक्षण का स्कैन दस्तावेज। खेत की स्थिति, सदस्य का "
            "नाम और अधिकारी की टिप्पणी OCR परीक्षण के लिए रखी गई है।"
        ),
    },
    {
        "language": "Hindi",
        "mode": "photo-ocr",
        "category": "भुगतान रसीद",
        "title": "भुगतान रसीद स्कैन",
        "body": (
            "छवि-आधारित भुगतान रसीद जिसमें जमा राशि, तारीख और शाखा कोड "
            "OCR ट्रांसक्रिप्ट सत्यापन के लिए उपलब्ध हैं।"
        ),
    },
    {
        "language": "Hindi",
        "mode": "photo-ocr",
        "category": "बैठक अभिलेख",
        "title": "समिति बैठक स्कैन",
        "body": (
            "समिति बैठक की स्कैन प्रति में प्रस्ताव, उपस्थित सदस्य और "
            "अनुवर्ती कार्य शामिल हैं। यह दस्तावेज फोटो OCR प्रवाह के लिए है।"
        ),
    },
    {
        "language": "Marathi",
        "mode": "unicode-text",
        "category": "कर्ज संचालन",
        "title": "सहकारी कर्ज मंजुरी सूचना",
        "body": (
            "सभासदाच्या कर्ज अर्जामध्ये ओळख पडताळणी, उत्पन्न तपशील आणि "
            "समितीची मंजुरी नोंदवली आहे. वितरणापूर्वी शाखा अधिकाऱ्याने "
            "सर्व पुरावे तपासावेत।"
        ),
    },
    {
        "language": "Marathi",
        "mode": "unicode-text",
        "category": "लेखा तपासणी",
        "title": "आतील लेखा तपासणी नोंद",
        "body": (
            "उघड तपासणी मुद्दे जोखीम पातळी, जबाबदार अधिकारी आणि अपेक्षित "
            "पूर्णता दिनांकासह चिन्हांकित केले आहेत. गंभीर मुद्दे मंडळासमोर "
            "मांडणे आवश्यक आहे."
        ),
    },
    {
        "language": "Marathi",
        "mode": "unicode-text",
        "category": "सभासद सेवा",
        "title": "सभासद सेवा अहवाल",
        "body": (
            "सेवा विनंती, निराकरण स्थिती आणि सभासदाला पाठवलेली सूचना "
            "नोंदवली आहे. प्रलंबित प्रकरणांची साप्ताहिक समीक्षा केली जाईल."
        ),
    },
    {
        "language": "Marathi",
        "mode": "photo-ocr",
        "category": "स्कॅन अभिलेख",
        "title": "पीक कर्ज तपासणी स्कॅन",
        "body": (
            "पीक कर्ज तपासणीचा छायाचित्र-आधारित दस्तऐवज. शेताची स्थिती, "
            "सभासदाचे नाव आणि अधिकाऱ्याची नोंद OCR चाचणीसाठी ठेवली आहे."
        ),
    },
    {
        "language": "Marathi",
        "mode": "photo-ocr",
        "category": "ठेव विभाग",
        "title": "ठेव पावती स्कॅन",
        "body": (
            "ठेव पावतीची प्रतिमा-आधारित प्रत ज्यात रक्कम, दिनांक आणि शाखा "
            "कोड OCR ट्रांसक्रिप्ट तपासणीसाठी वापरले जातात."
        ),
    },
]


def find_devanagari_font():
    explicit_path = os.environ.get("ADMIN_UI_DEMO_DEVANAGARI_FONT")
    candidates = [explicit_path] if explicit_path else []
    candidates.extend(DEVANAGARI_FONT_CANDIDATES)

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate

    font_roots = ("/usr/share/fonts", "/usr/local/share/fonts")
    for root in font_roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for font_path in root_path.rglob("*"):
            if font_path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            normalized = font_path.name.lower()
            if "devanagari" in normalized or "noto" in normalized or "lohit" in normalized:
                return str(font_path)
    return None


def insert_text_block(page, rect, text, *, fontsize, fontfile=None):
    kwargs = {
        "fontsize": fontsize,
        "lineheight": 1.35,
    }
    if fontfile:
        kwargs.update({"fontname": "devanagari", "fontfile": fontfile})
    page.insert_textbox(rect, text, **kwargs)


def make_pdf_bytes(title, body, *, language="English", mode="unicode-text", fontfile=None):
    document = fitz.open()
    page = document.new_page(width=PAGE_RECT.width, height=PAGE_RECT.height)
    page.draw_rect(PAGE_RECT + (18, 18, -18, -18), color=(0.82, 0.84, 0.86), width=1)
    page.draw_rect(fitz.Rect(48, 58, 547, 118), color=(0.1, 0.22, 0.32), fill=(0.93, 0.96, 0.98), width=0.8)

    heading = f"{title}\n{language} / {mode}"
    insert_text_block(page, fitz.Rect(72, 72, 523, 128), heading, fontsize=15, fontfile=fontfile)
    insert_text_block(page, BODY_RECT, body, fontsize=12, fontfile=fontfile)

    footer = "Admin UI fixture corpus - local development only"
    page.insert_textbox(fitz.Rect(72, 780, 523, 808), footer, fontsize=9, color=(0.35, 0.39, 0.43))

    if mode == "photo-ocr":
        scan_document = fitz.open()
        scan_page = scan_document.new_page(width=PAGE_RECT.width, height=PAGE_RECT.height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        scan_page.draw_rect(PAGE_RECT, fill=(0.97, 0.965, 0.94), color=None)
        scan_page.insert_image(fitz.Rect(28, 34, 567, 808), pixmap=pixmap)
        scan_page.draw_line((36, 54), (555, 50), color=(0.72, 0.73, 0.70), width=0.7)
        scan_page.draw_line((42, 806), (562, 812), color=(0.74, 0.73, 0.69), width=0.7)
        document.close()
        document = scan_document

    buffer = BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def cleanup_existing():
    for pdf in PDFFile.objects.filter(title__startswith=PDF_PREFIX):
        pdf.delete()
    for folder in Folder.objects.filter(name__startswith=CATEGORY_PREFIX):
        folder.delete()


def main():
    count = int(os.environ.get("ADMIN_UI_DEMO_COUNT", DEFAULT_COUNT))
    fixtures = FIXTURES[:count]
    username = os.environ.get("ADMIN_UI_DEMO_OWNER", "codex-admin")
    devanagari_font = find_devanagari_font()
    user_model = get_user_model()
    owner = user_model.objects.filter(username=username).first()
    if owner is None:
        owner = user_model.objects.create_user(
            username=username,
            password=user_model.objects.make_random_password(),
            role="admin",
            department="admin",
        )

    cleanup_existing()

    indexed_count = 0
    unknown_owner_count = 0
    language_counts = {}
    mode_counts = {}
    for index, fixture in enumerate(fixtures, start=1):
        language = fixture["language"]
        mode = fixture["mode"]
        folder = Folder.objects.create(
            name=f"{CATEGORY_PREFIX} {index:02d} - {fixture['category']} ({language}, {mode})",
            created_by=owner,
            keywords=[
                f"demo-{index:02d}",
                "operations",
                "cockpit",
                language.lower(),
                mode,
            ],
        )
        title = f"{PDF_PREFIX} {index:02d} - {language} {mode} - {fixture['title']}"
        body = (
            f"{fixture['body']}\n\n"
            f"Fixture number: {index:02d}\n"
            f"Language: {language}\n"
            f"Source mode: {mode}\n"
            "Use: Admin UI Operations Cockpit, category yard, receiving dock, "
            "document workbench, OCR transcript, and search-readiness testing."
        )
        pdf = PDFFile(
            title=title,
            folder=folder,
            uploaded_by=None if index % 5 == 0 else owner,
            indexed=mode == "unicode-text" or index % 2 == 0,
            extracted_text=body,
            text_content=body,
            page_chunks=[body],
            chunk_embeddings=[[0.01 * index, 0.02 * index, 0.03 * index]],
            keywords=["demo", "operations", "cockpit", language.lower(), mode],
        )
        pdf.file.save(
            f"demo-admin-ui-{index:02d}.pdf",
            ContentFile(
                make_pdf_bytes(
                    title,
                    body,
                    language=language,
                    mode=mode,
                    fontfile=devanagari_font if language in {"Hindi", "Marathi"} else None,
                )
            ),
            save=True,
        )
        indexed_count += 1 if pdf.indexed else 0
        unknown_owner_count += 1 if pdf.uploaded_by_id is None else 0
        language_counts[language] = language_counts.get(language, 0) + 1
        mode_counts[mode] = mode_counts.get(mode, 0) + 1

    print(
        "seeded admin UI demo data: "
        f"{len(fixtures)} categories, {len(fixtures)} PDFs, {indexed_count} indexed, "
        f"{unknown_owner_count} unknown owners, languages={language_counts}, "
        f"modes={mode_counts}, devanagari_font={devanagari_font or 'missing'}"
    )


if __name__ == "__main__":
    main()
