from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    path('livez', views.livez, name='livez'),
    path('readyz', views.readyz, name='readyz'),

    # ---------------- Public Pages ----------------
    path('', views.search_query, name='home'),
    path('search/', views.search_query, name='search_query'),

    # ---------------- Authentication ----------------
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("i18n/", include("django.conf.urls.i18n")),

    # ---------------- Dashboard ----------------
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/folder/<int:folder_id>/", views.dashboard, name="dashboard_folder"),
    path("dashboard/maintenance/", views.bulk_maintenance, name="bulk_maintenance"),
    path("dashboard/maintenance/<uuid:job_id>/action/", views.maintenance_job_action, name="maintenance_job_action"),

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
    path("pdf/<int:pdf_id>/public/", views.public_view_pdf, name="public_view_pdf"),
    path("pdf/<int:pdf_id>/view/", views.view_pdf, name="view_pdf"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
