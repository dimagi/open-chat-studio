from django.apps import AppConfig
from django.db.models.signals import pre_delete


class FilesConfig(AppConfig):
    name = "apps.files"
    label = "files"

    def ready(self):
        from .models import File

        for field in File._meta.get_fields():
            if not field.many_to_many:
                continue

            def delete_orphaned_files(sender, m2m_field=field, **kwargs):
                """Delete files when the related model is deleted."""
                instance = kwargs["instance"]
                getattr(instance, m2m_field.remote_field.name).all().delete()

            pre_delete.connect(delete_orphaned_files, sender=field.related_model)
