from django.urls import path

from vaultops import views


app_name = "vaultops"

urlpatterns = [
    path("state/", views.state_api, name="state"),
    path("profiles/", views.profiles_api, name="profiles"),
    path("generations/", views.generations_api, name="generations"),
    path("jobs/", views.jobs_api, name="jobs"),
    path("audit/", views.audit_api, name="audit"),
    path("sync/run/", views.sync_run, name="sync_run"),
    path("restores/start/", views.restore_start, name="restore_start"),
    path(
        "jobs/<uuid:job_id>/cancel/",
        views.job_cancel,
        name="job_cancel",
    ),
    path(
        "jobs/<uuid:job_id>/retry/",
        views.job_retry,
        name="job_retry",
    ),
    path(
        "profiles/<slug:profile_key>/probe/",
        views.profile_probe,
        name="profile_probe",
    ),
    path(
        "confirmations/issue/",
        views.confirmation_issue,
        name="confirmation_issue",
    ),
    path(
        "generations/<str:generation_id>/promote/",
        views.promote_generation,
        name="promote_generation",
    ),
    path(
        "activations/<uuid:workspace_id>/schedule/",
        views.schedule_activation_view,
        name="schedule_activation",
    ),
]
