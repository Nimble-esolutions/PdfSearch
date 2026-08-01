class VaultControlRouter:
    """Keep vault lifecycle authority projections in the stable control DB."""

    app_label = "vaultops"
    database_alias = "control"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.database_alias
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.database_alias
        return None

    def allow_relation(self, obj1, obj2, **hints):
        labels = {obj1._meta.app_label, obj2._meta.app_label}
        if self.app_label in labels:
            return labels == {self.app_label}
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            return db == self.database_alias
        if db == self.database_alias:
            # Other control-plane apps (for example dataops) may own tables
            # in this database. Let their router make that decision.
            if app_label == "dataops":
                return None
            return False
        return None
