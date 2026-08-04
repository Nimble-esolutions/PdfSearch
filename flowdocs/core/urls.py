from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from . import upload_views, views
from dataops import views as dataops_views

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url="/static/main/images/favicon.ico", permanent=True)),
    path('livez', views.livez, name='livez'),
    path('readyz', views.readyz, name='readyz'),
    path('health/data/', views.health_data, name='health_data'),
    path('health/lease/', views.health_lease, name='health_lease'),
    path('health/metrics/', views.metrics_view, name='health_metrics'),
    path('dashboard/operations/', dataops_views.workbench, name='operations_panel'),
    path('dashboard/data-operations/', include('dataops.urls')),
    path(
        'dashboard/operations/api/v1/',
        include('vaultops.urls'),
    ),
    path('dashboard/operations/data/', views.operations_data, name='operations_data'),
    path('dashboard/operations/lease/', views.operations_lease, name='operations_lease'),
    path("dashboard/operations/vault/", views.s3_operations_view, name="vault_operations"),
    path('dashboard/settings/', views.settings_view, name='settings'),
    path('dashboard/settings/save/', views.save_settings, name='save_settings'),
    path('robots.txt', views.robots_txt, name='robots_txt'),
    path('sitemap.xml', views.sitemap_xml, name='sitemap_xml'),

    # ---------------- Public Pages ----------------
    path('', views.search_query, name='home'),
    path('search/', views.search_query, name='search_query'),

    # ---------------- Legal / DPDA Compliance ----------------
    path("privacy/", views.privacy_view, name="privacy"),
    path("terms/", views.terms_view, name="terms"),
    path("data-policy/", views.data_policy_view, name="data_policy"),
    path("cookies/", views.cookie_policy_view, name="cookie_policy"),
    path("disclaimer/", views.disclaimer_view, name="disclaimer"),

    # ---------------- Authentication ----------------
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("i18n/", include("django.conf.urls.i18n")),

    # ---------------- Dashboard ----------------
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/folder/<int:folder_id>/", views.dashboard, name="dashboard_folder"),
    path(
        "dashboard/folder/<int:folder_id>/upload-batches/",
        upload_views.create_batch,
        name="upload_batch_create",
    ),
    path(
        "dashboard/upload-batches/<uuid:batch_id>/",
        upload_views.batch_detail,
        name="upload_batch_detail",
    ),
    path(
        "dashboard/upload-batches/<uuid:batch_id>/items/",
        upload_views.receive_batch_item,
        name="upload_batch_item",
    ),
    path(
        "dashboard/upload-batches/<uuid:batch_id>/items/<int:item_id>/remove/",
        upload_views.remove_batch_item,
        name="upload_batch_item_remove",
    ),
    path(
        "dashboard/upload-batches/<uuid:batch_id>/finalize/",
        upload_views.finalize_batch,
        name="upload_batch_finalize",
    ),
    path(
        "dashboard/upload-batches/<uuid:batch_id>/discard/",
        upload_views.discard_batch,
        name="upload_batch_discard",
    ),
    path("dashboard/maintenance/", views.bulk_maintenance, name="bulk_maintenance"),
    path("dashboard/maintenance/preview/", views.bulk_filter_preview, name="bulk_filter_preview"),
    path("dashboard/maintenance/<uuid:job_id>/action/", views.maintenance_job_action, name="maintenance_job_action"),
    path("dashboard/maintenance/<uuid:job_id>/audit/", views.job_audit_trail, name="job_audit_trail"),
    path("dashboard/maintenance/<uuid:job_id>/status/", views.job_status, name="job_status"),
    path("dashboard/maintenance/active/", views.active_jobs, name="active_jobs"),
    path("dashboard/generation/<str:generation_id>/validations/", views.generation_validations, name="generation_validations"),

    # ---------------- Generation Lifecycle ----------------
    path("dashboard/generation/<str:generation_id>/promote/", views.promote_generation, name="promote_generation"),
    path("dashboard/generation/<str:generation_id>/rollback/", views.rollback_generation, name="rollback_generation"),
    path("dashboard/generation/<str:generation_id>/purge/", views.purge_generation_view, name="purge_generation"),
    path("dashboard/generation/purge-expired/", views.purge_expired_generations_view, name="purge_expired_generations"),

    # ---------------- User Management ----------------
    path("dashboard/users/", views.user_list_view, name="user_list"),
    path("dashboard/users/<int:user_id>/edit/", views.edit_user, name="edit_user"),
    path("dashboard/users/toggle/<int:user_id>/", views.toggle_user_status, name="toggle_user_status"),
    path("dashboard/users/delete/<int:user_id>/", views.delete_user, name="delete_user"),

    # ---------------- Folder / Category ----------------
    path("folder/create/", views.create_folder, name="create_folder"),
    path("folder/<int:folder_id>/rename/", views.rename_folder, name="rename_folder"),
    path("folder/<int:folder_id>/delete/", views.delete_folder, name="delete_folder"),
    path("folder/<int:folder_id>/keywords/", views.update_folder_keywords, name="update_folder_keywords"),
    path("folder/<int:folder_id>/operations/", views.folder_operations, name="folder_operations"),

    # ---------------- Subcategory ----------------
    path("subcategory/add/", views.add_subcategory, name="add_subcategory"),

    # ---------------- PDF Management ----------------
    path("pdf/<int:pdf_id>/rename/", views.rename_pdf, name="rename_pdf"),
    path("pdf/<int:pdf_id>/owner/", views.assign_pdf_owner, name="assign_pdf_owner"),
    path("pdf/<int:file_id>/delete/", views.delete_pdf, name="delete_pdf"),
    path("pdf/<int:pdf_id>/deprecate/", views.deprecate_pdf_view, name="deprecate_pdf"),
    path("pdf/<int:pdf_id>/archive/", views.archive_pdf_view, name="archive_pdf"),
    path(
        "pdf/<int:pdf_id>/remove-from-search/",
        views.remove_pdf_from_search_view,
        name="remove_pdf_from_search",
    ),
    path(
        "pdf/<int:pdf_id>/retry-processing/",
        views.retry_pdf_processing_view,
        name="retry_pdf_processing",
    ),
    path("pdf/<int:pdf_id>/unavailable/", views.mark_pdf_unavailable_view, name="mark_pdf_unavailable"),
    path("pdf/<int:pdf_id>/recovery-evidence/", views.bind_pdf_recovery_evidence_view, name="bind_pdf_recovery_evidence"),
    path("pdf/<int:pdf_id>/restore/", views.restore_pdf_view, name="restore_pdf"),
    path("pdf/<int:pdf_id>/public/", views.public_view_pdf, name="public_view_pdf"),
    path("pdf/<int:pdf_id>/view/", views.view_pdf, name="view_pdf"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
