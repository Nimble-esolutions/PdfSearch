from django.urls import path

from . import views

app_name = "dataops"

urlpatterns = [
    path("v3/status/", views.v3_status, name="v3_status"),
    path("v3/operations/preview/", views.v3_operation_preview, name="v3_operation_preview"),
    path("v3/operations/", views.v3_operation_start, name="v3_operation_start"),
    path("v3/operations/<uuid:operation_id>/", views.v3_operation_status, name="v3_operation_status"),
    path("", views.workbench, name="workbench"),
    path("actions/", views.workbench_action, name="workbench_action"),
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
