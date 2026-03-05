from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [

    # ---------------- Public Pages ----------------
    path("", views.search_query, name="search"),
    path("search/", views.search_query, name="search_page"),

    # ---------------- Authentication ----------------
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("i18n/", include("django.conf.urls.i18n")),

    # ---------------- Dashboard ----------------
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/folder/<int:folder_id>/", views.dashboard, name="dashboard_folder"),

    # ---------------- User Management ----------------
    path("dashboard/users/", views.user_list_view, name="user_list"),
    path("dashboard/users/toggle/<int:user_id>/", views.toggle_user_status, name="toggle_user_status"),
    path("dashboard/users/delete/<int:user_id>/", views.delete_user, name="delete_user"),

    # ---------------- Folder / Category ----------------
    path("folder/create/", views.create_folder, name="create_folder"),
    path("folder/<int:folder_id>/rename/", views.rename_folder, name="rename_folder"),
    path("folder/<int:folder_id>/delete/", views.delete_folder, name="delete_folder"),
    path("folder/<int:folder_id>/keywords/", views.update_folder_keywords, name="update_folder_keywords"),

    # ---------------- Subcategory ----------------
    path("subcategory/add/", views.add_subcategory, name="add_subcategory"),

    # ---------------- PDF Management ----------------
    path("pdf/<int:pdf_id>/rename/", views.rename_pdf, name="rename_pdf"),
    path("pdf/<int:file_id>/delete/", views.delete_pdf, name="delete_pdf"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
