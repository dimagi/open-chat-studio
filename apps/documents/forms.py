from django import forms

from apps.documents.models import Collection
from apps.files.models import File
from apps.service_providers.models import LlmProviderTypes


class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ["name", "is_index", "llm_provider"]
        labels = {
            "is_index": "Create file index",
        }
        help_texts = {
            "is_index": "If checked, the files will be indexed and searchable using RAG.",
            "llm_provider": "This is the LLM provider at which the vector store will be created.",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["llm_provider"].queryset = request.team.llmprovider_set.filter(type=LlmProviderTypes.openai).all()

        if self.instance.id:
            self.fields["is_index"].widget.attrs["disabled"] = True
        else:
            self.fields["is_index"].widget.attrs = {"x-model": "isIndex"}


class FileForm(forms.ModelForm):
    class Meta:
        model = File
        fields = ["name", "summary", "file"]
        help_texts = {
            "summary": "This is only needed when the file will not be used for RAG",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].widget.attrs.update({"class": "file-input"})
