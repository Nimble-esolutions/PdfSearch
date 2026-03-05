from django.urls import path, include
from . import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [

    # =========================
    # Public Search
    # =========================

    path('', views.search_query, name='search_query'),
    path('search/', views.search_query, name='search_query'),

    # =========================
    # Authentication
    # =========================
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path("i18n/", include("django.conf.urls.i18n")),

    # =========================
    # Dashboard
    # =========================
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/<int:folder_id>/', views.dashboard, name='dashboard_folder'),

    # =========================
    # User Management
    # =========================
    path('dashboard/users/', views.user_list_view, name='user_list'),
    path('dashboard/users/toggle/<int:user_id>/', views.toggle_user_status, name='toggle_user_status'),
    path('dashboard/users/delete/<int:user_id>/', views.delete_user, name='delete_user'),

    # =========================
    # Folder Management
    # =========================
    path('create-folder/', views.create_folder, name='create_folder'),
    path('folder/<int:folder_id>/rename/', views.rename_folder, name='rename_folder'),
    path('folder/<int:folder_id>/delete/', views.delete_folder, name='delete_folder'),
    path('add-subcategory/', views.add_subcategory, name='add_subcategory'),

    # =========================
    # PDF Management
    # =========================
    path('rename-pdf/<int:pdf_id>/', views.rename_pdf, name='rename_pdf'),
    path('delete-pdf/<int:file_id>/', views.delete_pdf, name='delete_pdf'),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
