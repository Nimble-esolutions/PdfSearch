from django.urls import path

from . import views

app_name = "dataops"

urlpatterns = [
    path("", views.workbench, name="workbench"),
    path("state/", views.state_api, name="state"),
    path("refresh/", views.refresh, name="refresh"),
    path("backup/", views.backup, name="backup"),
    path("restore/", views.restore, name="restore"),
    path("clone-rebind/", views.clone_rebind, name="clone_rebind"),
    path("preflight/", views.preflight, name="preflight"),
    path("repair/", views.refresh, name="repair"),
    path("configuration/", views.configuration, name="configuration"),
    path("profiles/<slug:profile_key>/probe/", views.profile_probe, name="profile_probe"),
    path("jobs/", views.jobs, name="jobs"),
    path("jobs/<slug:job_slug>/run/", views.run_job, name="run_job"),
    path("jobs/<slug:job_slug>/deletions/preview/", views.preview_job_deletions, name="preview_job_deletions"),
    path("jobs/<slug:job_slug>/deletions/<uuid:preview_id>/confirm/", views.confirm_job_deletions, name="confirm_job_deletions"),
    path("jobs/quarantines/<uuid:operation_id>/recover/", views.recover_job_quarantine, name="recover_job_quarantine"),
    path("advanced/", views.advanced, name="advanced"),
    path("env-patch/", views.env_patch, name="env_patch"),
]
