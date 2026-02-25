# core/urls.py
from django.urls import path
from .views import (
    admin_dashboard,
    upload_pdf_view,
    rebuild_faiss_view,
    ask_question_view,
    ask_page
)

urlpatterns = [
    path("", admin_dashboard, name="admin_dashboard"),

    # UI pages
    path("ask-page/", ask_page, name="ask_page"),

    # APIs
    path("ask/", ask_question_view, name="ask_question"),
    path("upload-pdf/", upload_pdf_view),
    # path("rebuild-faiss/<int:pdf_id>/", rebuild_faiss_view),
    path('rebuild-faiss/', rebuild_faiss_view, name='rebuild-faiss'),
]
