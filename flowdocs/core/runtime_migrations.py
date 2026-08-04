"""Fail-closed classification and application of active-runtime migrations.

Activated runtimes are writable application databases, but a release must not
silently execute data-moving or destructive schema operations against them.
This module permits only additive table/index creation and state-only metadata
changes. Everything else stays on the isolated candidate-migration path.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import connections, migrations
from django.db.migrations.executor import MigrationExecutor


@dataclass(frozen=True)
class RuntimeMigrationDecision:
    migration: str
    operation: str
    safe: bool
    reason: str


_ADDITIVE_OPERATIONS = (
    migrations.CreateModel,
    migrations.AddIndex,
)
_STATE_ONLY_OPERATIONS = (
    migrations.AlterModelOptions,
    migrations.AlterModelManagers,
)


def _field_database_change(operation, app_label, old_state, new_state, connection):
    old_model = old_state.apps.get_model(app_label, operation.model_name)
    new_model = new_state.apps.get_model(app_label, operation.model_name)
    old_field = old_model._meta.get_field(operation.name)
    new_field = new_model._meta.get_field(operation.name)
    schema_editor = connection.schema_editor(collect_sql=True)
    return schema_editor._field_should_be_altered(old_field, new_field)


def classify_operation(
    *, app_label, migration_name, operation, old_state, new_state, connection
):
    operation_name = operation.__class__.__name__
    migration = f"{app_label}.{migration_name}"
    if isinstance(operation, _ADDITIVE_OPERATIONS):
        return RuntimeMigrationDecision(
            migration, operation_name, True, "additive_schema"
        )
    if isinstance(operation, _STATE_ONLY_OPERATIONS):
        return RuntimeMigrationDecision(
            migration, operation_name, True, "state_only"
        )
    if isinstance(operation, migrations.AlterField) and not _field_database_change(
        operation, app_label, old_state, new_state, connection
    ):
        return RuntimeMigrationDecision(
            migration, operation_name, True, "state_only_field_metadata"
        )
    return RuntimeMigrationDecision(
        migration, operation_name, False, "isolated_candidate_required"
    )


def runtime_migration_plan(*, using="default"):
    connection = connections[using]
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    state = executor._create_project_state(with_applied_migrations=True)
    decisions = []
    for migration, backwards in plan:
        migration_name = f"{migration.app_label}.{migration.name}"
        if backwards:
            decisions.append(
                RuntimeMigrationDecision(
                    migration_name,
                    "BackwardMigration",
                    False,
                    "isolated_candidate_required",
                )
            )
            continue
        for operation in migration.operations:
            old_state = state.clone()
            operation.state_forwards(migration.app_label, state)
            decisions.append(
                classify_operation(
                    app_label=migration.app_label,
                    migration_name=migration.name,
                    operation=operation,
                    old_state=old_state,
                    new_state=state,
                    connection=connection,
                )
            )
    return decisions
