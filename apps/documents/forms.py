from django import forms

from apps.documents.models import Collection


class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ["name", "is_index", "llm_provider", "embedding_provider_model", "is_remote_index"]
        labels = {
            "is_index": "Create file index",
        }
        help_texts = {
            "is_index": "If checked, the files will be indexed and searchable using RAG",
            "llm_provider": "The provider whose embedding model will be used for indexing",
            "embedding_provider_model": "The model used to create embeddings",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["llm_provider"].queryset = request.team.llmprovider_set.all()
        self.fields["embedding_provider_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"

        # Alpine.js bindings
        self.fields["is_index"].widget.attrs = {"x-model": "isIndex"}
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "selectedLlmProviderId",
        }

        if self.instance.id:
            self.fields["is_index"].widget.attrs["disabled"] = True
            if self.instance.has_pending_index_uploads():
                self.fields["llm_provider"].widget.attrs["disabled"] = True

            if self.instance.is_index:
                self.fields["embedding_provider_model"].widget.attrs["disabled"] = True
                self.fields["is_remote_index"].widget.attrs["disabled"] = True

    def clean_is_index(self):
        if self.instance.id:
            return self.instance.is_index
        return self.cleaned_data["is_index"]

    def clean_is_remote_index(self):
        if self.instance.id:
            return self.instance.is_remote_index
        return self.cleaned_data["is_remote_index"]

    def clean_llm_provider(self):
        if self.cleaned_data["is_index"] and not self.cleaned_data["llm_provider"]:
            raise forms.ValidationError("This field is required when creating an index.")
        return self.cleaned_data["llm_provider"]
