import pydantic
from django import forms
from django.conf import settings
from django.db.models import Q, Subquery

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.documents.datamodels import ConfluenceSourceConfig, DocumentSourceConfig, GitHubSourceConfig
from apps.documents.models import Collection, DocumentSource, SourceType
from apps.service_providers.models import AuthProvider, AuthProviderType, EmbeddingProviderModel
from apps.utils.urlvalidate import InvalidURL, validate_user_input_url


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
    requires_auth = True
    allowed_auth_types = []
    auth_provider_help = ""

    class Meta:
        model = DocumentSource
        fields = ["source_type", "auto_sync_enabled", "auth_provider"]
        labels = {
            "auto_sync_enabled": "Auto Sync",
        }
        widgets = {"source_type": forms.HiddenInput()}

    def __init__(self, collection, *args, **kwargs):
        self.collection = collection
        instance = kwargs.get("instance")
        initial = kwargs.get("initial")
        if instance and initial is not None:
            object_data = self._get_config_from_instance(instance).model_dump()
            kwargs["initial"] = {**object_data, **initial}
        super().__init__(*args, **kwargs)
        if not self.requires_auth:
            del self.fields["auth_provider"]
        else:
            self.fields["auth_provider"].help_text = self.auth_provider_help
            self.fields["auth_provider"].queryset = AuthProvider.objects.filter(
                team_id=collection.team_id, type__in=self.allowed_auth_types
            )

    def _get_config_from_instance(self, instance):
        raise NotImplementedError

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.config = self.cleaned_data["config"]
        instance.collection = self.collection
        instance.team = self.collection.team

        if commit:
            instance.save()
        return instance


class GithubDocumentSourceForm(DocumentSourceForm):
    requires_auth = True
    allowed_auth_types = [AuthProviderType.bearer]
    auth_provider_help = "GitHub requires Bearer Auth"

    repo_url = forms.URLField(
        label="Repository URL",
        help_text="GitHub repository URL (e.g., https://github.com/user/repo)",
        widget=forms.URLInput(attrs={"placeholder": "https://github.com/user/repo"}),
    )
    branch = forms.CharField(
        initial="main",
        label="Branch",
        help_text="Git branch to sync from",
        widget=forms.TextInput(attrs={"placeholder": "main"}),
    )
    file_pattern = forms.CharField(
        required=False,
        initial="*.md",
        label="File Pattern",
        help_text="File patterns to include. Prefix with '!' to exclude matching files. "
        "(comma-separated, e.g., *.md, *.txt, !test_*)",
        widget=forms.TextInput(attrs={"placeholder": "*.md, *.txt"}),
    )
    path_filter = forms.CharField(
        required=False,
        label="Path Filter",
        help_text="Optional path prefix to filter files (e.g., docs/)",
        widget=forms.TextInput(attrs={"placeholder": "docs/"}),
    )

    def _get_config_from_instance(self, instance):
        return instance.config.github

    def clean_repo_url(self):
        github_repo_url = self.cleaned_data["repo_url"]
        try:
            validate_user_input_url(github_repo_url, strict=not settings.DEBUG)
        except InvalidURL as e:
            raise forms.ValidationError(f"The URL is invalid: {str(e)}") from None

        return github_repo_url

    def clean_source_type(self):
        source_type = self.cleaned_data.get("source_type")
        if source_type != SourceType.GITHUB:
            raise forms.ValidationError(f"Expected GitHub source type, got {source_type}")
        return source_type

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        repo_url = cleaned_data.get("repo_url")
        branch = cleaned_data.get("branch", "main")
        file_pattern = cleaned_data.get("file_pattern", "")
        path_filter = cleaned_data.get("path_filter", "")

        try:
            github_config = GitHubSourceConfig(
                repo_url=repo_url, branch=branch, file_pattern=file_pattern, path_filter=path_filter
            )
        except pydantic.ValidationError:
            raise forms.ValidationError("Invalid config") from None

        cleaned_data["config"] = DocumentSourceConfig(github=github_config)
        return cleaned_data


class ConfluenceDocumentSourceForm(DocumentSourceForm):
    requires_auth = True
    allowed_auth_types = [AuthProviderType.basic]
    auth_provider_help = "Confluence requires a 'Basic' authentication provider"
    custom_template = "documents/partials/confluence_form.html"

    base_url = forms.URLField(
        label="Confluence Site URL",
        help_text="Confluence Site URL (e.g., https://yoursite.atlassian.com/wiki)",
        widget=forms.URLInput(attrs={"placeholder": "https://yoursite.atlassian.com/wiki"}),
    )

    # Loading options - only one should be filled
    space_key = forms.CharField(
        required=False,
        label="Space Key",
        help_text="Confluence Space Key (e.g., 'DOCS')",
        widget=forms.TextInput(attrs={"placeholder": "DOCS"}),
    )
    label = forms.CharField(
        required=False,
        label="Label",
        help_text="Confluence label to filter pages",
        widget=forms.TextInput(attrs={"placeholder": "documentation"}),
    )
    cql = forms.CharField(
        required=False,
        label="CQL Query",
        help_text="Confluence Query Language query",
        widget=forms.Textarea(attrs={"placeholder": "space = IDEAS or label = idea", "rows": 3}),
    )
    page_ids = forms.CharField(
        required=False,
        label="Page IDs",
        help_text="Comma-separated list of specific page IDs",
        widget=forms.TextInput(attrs={"placeholder": "12345, 67890, 11223"}),
    )

    # Additional options
    max_pages = forms.IntegerField(
        initial=1000,
        min_value=1,
        max_value=10000,
        label="Max Pages",
        help_text="Maximum number of pages to load (1-10000)",
    )

    def _get_config_from_instance(self, instance):
        return instance.config.confluence

    def clean_base_url(self):
        base_url = self.cleaned_data["base_url"]
        try:
            validate_user_input_url(base_url, strict=not settings.DEBUG)
        except InvalidURL as e:
            raise forms.ValidationError(f"The URL is invalid: {str(e)}") from None

        return base_url

    def clean_source_type(self):
        source_type = self.cleaned_data.get("source_type")
        if source_type != SourceType.CONFLUENCE:
            raise forms.ValidationError(f"Expected Confluence source type, got {source_type}")
        return source_type

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        base_url = cleaned_data.get("base_url")
        space_key = cleaned_data.get("space_key", "")
        label = cleaned_data.get("label", "")
        cql = cleaned_data.get("cql", "")
        page_ids = cleaned_data.get("page_ids", "")
        max_pages = cleaned_data.get("max_pages", 1000)

        # Validate that exactly one loading option is specified
        options = [space_key, label, cql, page_ids]
        non_empty_options = [opt for opt in options if opt and opt.strip()]

        if len(non_empty_options) == 0:
            raise forms.ValidationError(
                "At least one loading option must be specified: Space Key, Label, CQL Query, or Page IDs"
            )
        if len(non_empty_options) > 1:
            raise forms.ValidationError("Only one loading option can be specified at a time")

        # Validate page_ids format if specified
        if page_ids.strip():
            try:
                [int(pid.strip()) for pid in page_ids.split(",") if pid.strip()]
            except ValueError:
                raise forms.ValidationError({"page_ids": "Page IDs must be comma-separated integers"}) from None

        try:
            config = ConfluenceSourceConfig(
                base_url=base_url,
                space_key=space_key,
                label=label,
                cql=cql,
                page_ids=page_ids,
                max_pages=max_pages,
            )
        except pydantic.ValidationError as e:
            raise forms.ValidationError(f"Invalid config: {str(e)}") from None

        cleaned_data["config"] = DocumentSourceConfig(confluence=config)
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
