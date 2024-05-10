from django.forms import BaseModelFormSet, modelformset_factory

from apps.files.models import File


class BaseFileFormSet(BaseModelFormSet):
    def save(self, request):
        files = super().save(commit=False)
        for file in files:
            file.team = request.team
            file.save()
        return files


def get_file_formset(request, formset_cls=None, prefix=""):
    formset_cls = formset_cls or BaseFileFormSet
    kwargs = {}
    if request.method in ("POST", "PUT"):
        kwargs.update(
            {
                "data": request.POST,
                "files": request.FILES,
            }
        )

    FileFormSet = modelformset_factory(
        File, formset=formset_cls, fields=("file",), can_delete=True, can_delete_extra=True, extra=0
    )
    return FileFormSet(queryset=File.objects.none(), prefix=f"{prefix}files", **kwargs)
