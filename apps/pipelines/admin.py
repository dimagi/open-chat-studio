import json

from django import forms
from django.contrib import admin

from .models import Node, Pipeline, PipelineChatHistory, PipelineChatMessages


class PipelineNodeInline(admin.TabularInline):
    model = Node
    extra = 0


class PrettyJSONEncoder(json.JSONEncoder):
    def __init__(self, *args, indent, sort_keys, **kwargs):
        super().__init__(*args, indent=4, sort_keys=True, **kwargs)


class PipelineAdminForm(forms.ModelForm):
    data = forms.JSONField(encoder=PrettyJSONEncoder)


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    form = PipelineAdminForm
    inlines = [PipelineNodeInline]


class PipelineChatMessagesInline(admin.TabularInline):
    model = PipelineChatMessages
    extra = 0

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.order_by("-created_at")


@admin.register(PipelineChatHistory)
class PipelineChatHistoryAdmin(admin.ModelAdmin):
    inlines = [PipelineChatMessagesInline]
