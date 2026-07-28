from django.urls import path

from vaultops import views


app_name = "vaultops"

urlpatterns = [
    path("state/", views.state_api, name="state"),
    path("profiles/", views.profiles_api, name="profiles"),
    path(
        "profiles/configure/",
        views.profile_configure,
        name="profile_configure",
    ),
    path("generations/", views.generations_api, name="generations"),
    path("jobs/", views.jobs_api, name="jobs"),
    path("audit/", views.audit_api, name="audit"),
    path("diagnostics/", views.diagnostics_api, name="diagnostics"),
    path("retention/", views.retention_api, name="retention"),
    path("gc-plans/", views.gc_plans_api, name="gc_plans"),
    path("sync/run/", views.sync_run, name="sync_run"),
    path(
        "maintenance/plans/",
        views.maintenance_plan_create,
        name="maintenance_plan_create",
    ),
    path(
        "maintenance/plans/<uuid:plan_id>/queue/",
        views.maintenance_plan_queue,
        name="maintenance_plan_queue",
    ),
    path(
        "maintenance/jobs/<uuid:job_id>/cancel/",
        views.maintenance_job_cancel,
        name="maintenance_job_cancel",
    ),
    path(
        "maintenance/jobs/<uuid:job_id>/retry/",
        views.maintenance_job_retry,
        name="maintenance_job_retry",
    ),
    path(
        "maintenance/jobs/<uuid:job_id>/prepare-activation/",
        views.maintenance_candidate_prepare,
        name="maintenance_candidate_prepare",
    ),
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
        "profiles/<slug:profile_key>/inventory/",
        views.profile_inventory,
        name="profile_inventory",
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
        "generations/<str:generation_id>/retire/",
        views.retire_generation_view,
        name="retire_generation",
    ),
    path(
        "generations/<str:generation_id>/unretire/",
        views.unretire_generation_view,
        name="unretire_generation",
    ),
    path(
        "generations/<str:generation_id>/holds/",
        views.retention_hold_create,
        name="retention_hold_create",
    ),
    path(
        "retention/holds/<int:hold_id>/release/",
        views.retention_hold_release,
        name="retention_hold_release",
    ),
    path(
        "gc-plans/create/",
        views.gc_plan_create,
        name="gc_plan_create",
    ),
    path(
        "gc-plans/<uuid:plan_id>/execute/",
        views.gc_plan_execute,
        name="gc_plan_execute",
    ),
    path(
        "activations/<uuid:workspace_id>/schedule/",
        views.schedule_activation_view,
        name="schedule_activation",
    ),
    path(
        "activations/rollback/confirm/",
        views.rollback_confirmation_issue,
        name="rollback_confirmation_issue",
    ),
    path(
        "activations/<uuid:workspace_id>/rollback/",
        views.schedule_rollback_view,
        name="schedule_rollback",
    ),
]
