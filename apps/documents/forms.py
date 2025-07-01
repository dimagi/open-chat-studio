from django import forms

from apps.assistants.models import OpenAiAssistant, ToolResources
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
        widgets = {"is_index": forms.HiddenInput()}

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["llm_provider"].queryset = request.team.llmprovider_set.all()
        self.fields["embedding_provider_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"

        # Alpine.js bindings
        self.fields["is_index"].widget.attrs = {"x-model": "isIndex"}
        self.fields["is_remote_index"].widget.attrs = {"x-model": "isRemoteIndex"}
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "selectedLlmProviderId",
        }

        if self.instance.id:
            # Changing the collection type is not allowed
            self.fields["is_index"].widget.attrs["disabled"] = True

            if self.instance.is_index:
                # Changing the index location is not allowed
                self.fields["is_remote_index"].widget.attrs["disabled"] = True

                if self.instance.has_pending_index_uploads():
                    self.fields["llm_provider"].widget.attrs["disabled"] = True

                if not self.instance.is_remote_index:
                    # We don't yet support changing the embedding model or llm provider for local indexes
                    self.fields["embedding_provider_model"].widget.attrs["disabled"] = True
                    self.fields["llm_provider"].widget.attrs["disabled"] = True

    def clean_is_index(self):
        if self.instance.id:
            return self.instance.is_index
        return self.cleaned_data["is_index"]

    def clean_is_remote_index(self):
        if self.instance.id:
            return self.instance.is_remote_index
        return self.cleaned_data["is_remote_index"]

    def clean(self):
        cleaned_data = super().clean()
        is_index = self.cleaned_data["is_index"]
        llm_provider = self.cleaned_data["llm_provider"]
        is_remote_index = self.cleaned_data["is_remote_index"]
        embedding_provider_model = self.cleaned_data["embedding_provider_model"]

        if is_index:
            if not llm_provider:
                raise forms.ValidationError({"llm_provider": "This field is required when creating an index."})

            if is_remote_index:
                self.cleaned_data["embedding_provider_model"] = None
            elif not embedding_provider_model:
                raise forms.ValidationError(
                    {"embedding_provider_model": "Local indexes require an embedding model to be selected."}
                )
        else:
            # Clear these fields incase they were selected
            self.cleaned_data["llm_provider"] = None
            self.cleaned_data["embedding_provider_model"] = None
            self.cleaned_data["is_remote_index"] = False

        return cleaned_data


class CreateCollectionFromAssistantForm(forms.Form):
    assistant = forms.ModelChoiceField(
        queryset=OpenAiAssistant.objects.none(),
        label="Assistant",
        help_text="Select an assistant with file search enabled to create a collection from",
    )
    collection_name = forms.CharField(
        max_length=255,
        label="Collection Name",
        help_text="Enter a name for the new collection",
    )

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter assistants that have file search enabled and have file search resources
        self.fields["assistant"].queryset = (
            OpenAiAssistant.objects.filter(
                team=request.team,
                is_archived=False,
                working_version_id=None,
                builtin_tools__contains=["file_search"],
            )
            .filter(
                id__in=ToolResources.objects.filter(
                    tool_type="file_search",
                    files__isnull=False,
                ).values_list("assistant_id", flat=True)
            )
            .distinct()
        )

    def clean_assistant(self):
        assistant = self.cleaned_data["assistant"]
        if not assistant:
            raise forms.ValidationError("Please select an assistant.")

        # Verify the assistant has file search tool resources
        file_search_resources = assistant.tool_resources.filter(tool_type="file_search")
        if not file_search_resources.exists():
            raise forms.ValidationError("The selected assistant does not have file search enabled or configured.")

        # Verify the assistant has files
        has_files = file_search_resources.filter(files__isnull=False).exists()
        if not has_files:
            raise forms.ValidationError("The selected assistant does not have any files for file search.")

        return assistant

    def clean_collection(self):
        collection_name = self.cleaned_data["collection_name"]
        if Collection.objects.filter(team=self.request.team, name=collection_name, is_version=False).exists():
            raise forms.ValidationError("A collection with this name already exists.")
