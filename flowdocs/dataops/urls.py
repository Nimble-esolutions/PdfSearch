from django.urls import path

from . import views

app_name = "dataops"

urlpatterns = [
    path("", views.workbench, name="workbench"),
    path("state/", views.state_api, name="state"),
    path("refresh/", views.refresh, name="refresh"),
    path("backup/", views.backup, name="backup"),
    path("restore/", views.restore, name="restore"),
    path("preflight/", views.preflight, name="preflight"),
    path("repair/", views.refresh, name="repair"),
    path("configuration/", views.configuration, name="configuration"),
    path("env-patch/", views.env_patch, name="env_patch"),
]
