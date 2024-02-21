import logging

from django.apps import AppConfig
from django.db.models.signals import pre_delete

from apps.utils.deletion import get_related_m2m_objects

log = logging.getLogger("files")


class FilesConfig(AppConfig):
    name = "apps.files"
    label = "files"

    def ready(self):
        from .models import File

        for field in File._meta.get_fields():
            if not field.many_to_many:
                continue

            def delete_orphaned_files(sender, m2m_field=field, **kwargs):
                """Delete files when the related model is deleted and there are no other references."""
                instance = kwargs["instance"]
                files = getattr(instance, m2m_field.remote_field.name).all()
                related = get_related_m2m_objects(files, exclude=[instance])
                if not related:
                    files.delete()
                else:
                    to_keep = [file.id for file in files if file in related]
                    log.warning("Unable to delete files referenced by multiple objects: %s", to_keep)

                    to_delete = [file.id for file in files if file not in related]
                    File.objects.filter(id__in=to_delete).delete()

            pre_delete.connect(delete_orphaned_files, sender=field.related_model)
