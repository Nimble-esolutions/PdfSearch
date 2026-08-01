class DataOpsControlRouter:
    """Keep Data Operations control records in the disposable control DB."""

    app_label = "dataops"
    database_alias = "control"

    def db_for_read(self, model, **hints):
        return self.database_alias if model._meta.app_label == self.app_label else None

    def db_for_write(self, model, **hints):
        return self.database_alias if model._meta.app_label == self.app_label else None

    def allow_relation(self, obj1, obj2, **hints):
        labels = {obj1._meta.app_label, obj2._meta.app_label}
        if self.app_label in labels:
            return labels == {self.app_label}
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            return db == self.database_alias
        if db == self.database_alias:
            # Preserve the legacy Vault control app's ownership while this
            # router participates in the same control database.
            if app_label == "vaultops":
                return None
            return False
        return None
