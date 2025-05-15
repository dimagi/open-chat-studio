from django import forms

from apps.documents.models import Collection
from apps.service_providers.models import LlmProviderTypes


class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ["name", "is_index", "llm_provider"]
        labels = {
            "is_index": "Create file index",
        }
        help_texts = {
            "is_index": "If checked, the files will be indexed and searchable using RAG",
            "llm_provider": "This is the LLM provider at which the vector store will be created.",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["llm_provider"].queryset = request.team.llmprovider_set.filter(type=LlmProviderTypes.openai).all()

        self.fields["is_index"].widget.attrs = {"x-model": "isIndex"}
        if self.instance.id:
            self.fields["is_index"].widget.attrs["disabled"] = True

        if self.instance.is_index:
            self.fields["llm_provider"].widget.is_required = True

    def clean_is_index(self):
        if self.instance.id:
            return self.instance.is_index
        return self.cleaned_data["is_index"]

    def clean_llm_provider(self):
        if self.cleaned_data["is_index"] and not self.cleaned_data["llm_provider"]:
            raise forms.ValidationError("This field is required when creating an index.")
        return self.cleaned_data["llm_provider"]
