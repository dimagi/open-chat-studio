from django.contrib import admin

from .models import Pipeline, PipelineChatHistory, PipelineChatMessages, PipelineRun


class PipelineRunInline(admin.TabularInline):
    model = PipelineRun
    extra = 0


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    inlines = [PipelineRunInline]


class PipelineChatMessagesInline(admin.TabularInline):
    model = PipelineChatMessages
    extra = 0

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.order_by("-created_at")


@admin.register(PipelineChatHistory)
class PipelineChatHistoryAdmin(admin.ModelAdmin):
    inlines = [PipelineChatMessagesInline]
