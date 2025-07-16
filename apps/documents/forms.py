from django import forms
from django.db.models import Q, Subquery

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.documents.models import Collection, DocumentSource, DocumentSourceConfig, GitHubSourceConfig, SourceType
from apps.service_providers.models import EmbeddingProviderModel


class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = [
            "name",
            "is_index",
            "llm_provider",
            "embedding_provider_model",
            "is_remote_index",
        ]
        labels = {
            "is_index": "Create file index",
            "is_remote_index": "Use the provider hosted index",
        }
        help_texts = {
            "is_index": "If checked, the files will be indexed and searchable using RAG",
            "llm_provider": "The provider whose embedding model will be used for indexing",
            "embedding_provider_model": "The model to use to create embeddings for the files in this collection",
        }
        widgets = {"is_index": forms.HiddenInput()}

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        embedding_model_provider_queryset = EmbeddingProviderModel.objects.filter(
            Q(team_id=None) | Q(team_id=request.team.id)
        )

        embedding_model_provider_types = embedding_model_provider_queryset.values_list("type").distinct()
        self.fields["llm_provider"].queryset = request.team.llmprovider_set.filter(
            type__in=Subquery(embedding_model_provider_types)
        ).all()

        self.fields["embedding_provider_model"].queryset = embedding_model_provider_queryset
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
        llm_provider = cleaned_data.get("llm_provider")
        is_remote_index = cleaned_data["is_remote_index"]
        embedding_provider_model = cleaned_data.get("embedding_provider_model")

        if self.instance.id:
            if not llm_provider:
                llm_provider = self.instance.llm_provider
                self.cleaned_data["llm_provider"] = llm_provider
            if not embedding_provider_model:
                embedding_provider_model = self.instance.embedding_provider_model
                self.cleaned_data["embedding_provider_model"] = embedding_provider_model

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


class DocumentSourceForm(forms.ModelForm):
    # GitHub configuration fields
    github_repo_url = forms.URLField(
        required=False,
        label="Repository URL",
        help_text="GitHub repository URL (e.g., https://github.com/user/repo)",
        widget=forms.URLInput(attrs={"placeholder": "https://github.com/user/repo"}),
    )
    github_branch = forms.CharField(
        required=False,
        initial="main",
        label="Branch",
        help_text="Git branch to sync from",
        widget=forms.TextInput(attrs={"placeholder": "main"}),
    )
    github_file_pattern = forms.CharField(
        required=False,
        initial="*.md",
        label="File Pattern",
        help_text="File patterns to include (comma-separated, e.g., *.md, *.txt)",
        widget=forms.TextInput(attrs={"placeholder": "*.md, *.txt"}),
    )
    github_path_filter = forms.CharField(
        required=False,
        label="Path Filter",
        help_text="Optional path prefix to filter files (e.g., docs/)",
        widget=forms.TextInput(attrs={"placeholder": "docs/"}),
    )

    class Meta:
        model = DocumentSource
        fields = ["source_type", "auto_sync_enabled"]
        labels = {
            "source_type": "Source Type",
            "auto_sync_enabled": "Auto Sync",
        }
        help_texts = {
            "source_type": "Type of document source to configure",
            "auto_sync_enabled": "Automatically sync this source on a schedule",
        }

    def __init__(self, collection, *args, **kwargs):
        self.collection = collection
        super().__init__(*args, **kwargs)

        # Set up form attributes for JavaScript
        self.fields["source_type"].widget.attrs = {"x-model": "sourceType", "x-on:change": "sourceTypeChanged"}

        # Initialize form with existing data if editing
        if self.instance.pk and self.instance.source_config:
            if self.instance.source_type == SourceType.GITHUB and self.instance.config.github:
                github_config = self.instance.config.github
                self.fields["github_repo_url"].initial = github_config.repo_url
                self.fields["github_branch"].initial = github_config.branch
                self.fields["github_file_pattern"].initial = github_config.file_pattern
                self.fields["github_path_filter"].initial = github_config.path_filter

    def clean(self):
        cleaned_data = super().clean()
        source_type = cleaned_data.get("source_type")

        if source_type == SourceType.GITHUB:
            # Validate GitHub fields
            repo_url = cleaned_data.get("github_repo_url")
            if not repo_url:
                raise forms.ValidationError({"github_repo_url": "Repository URL is required for GitHub sources."})

            branch = cleaned_data.get("github_branch", "main")
            file_pattern = cleaned_data.get("github_file_pattern", "*.md")
            path_filter = cleaned_data.get("github_path_filter", "")

            # Create GitHub config
            github_config = GitHubSourceConfig(
                repo_url=repo_url, branch=branch, file_pattern=file_pattern, path_filter=path_filter
            )

            # Store in config field
            cleaned_data["config"] = DocumentSourceConfig(github=github_config)

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.collection = self.collection
        instance.team = self.collection.team

        if commit:
            instance.save()
        return instance


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
