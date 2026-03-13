from django import forms

from apps.files.models import File


class BaseFileFormSet(forms.BaseModelFormSet):
    def save(self, request):  # ty: ignore[invalid-method-override]
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

    FileFormSet = forms.modelformset_factory(
        File, formset=formset_cls, fields=("file",), can_delete=True, can_delete_extra=True, extra=0
    )
    return FileFormSet(queryset=File.objects.none(), prefix=f"{prefix}files", **kwargs)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, list | tuple):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = [single_file_clean(data, initial)]
        return result


class MultipleFileFieldForm(forms.Form):
    file = MultipleFileField()


class FileForm(forms.ModelForm):
    class Meta:
        model = File
        fields = ["name", "summary"]
        help_texts = {
            "summary": "This is only needed when the file will not be used for RAG",
        }
