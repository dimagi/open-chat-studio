from django import forms
from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from ..utils.json import PrettyJSONEncoder
from .models import Node, Pipeline, PipelineChatHistory, PipelineChatMessages


class PipelineNodeInline(ReadonlyAdminMixin, admin.TabularInline):
    model = Node
    extra = 0


class PipelineAdminForm(forms.ModelForm):
    data = forms.JSONField(encoder=PrettyJSONEncoder)


@admin.register(Pipeline)
class PipelineAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    form = PipelineAdminForm
    inlines = [PipelineNodeInline]


class PipelineChatMessagesInline(ReadonlyAdminMixin, admin.TabularInline):
    model = PipelineChatMessages
    extra = 0

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.order_by("-created_at")


@admin.register(PipelineChatHistory)
class PipelineChatHistoryAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    inlines = [PipelineChatMessagesInline]
