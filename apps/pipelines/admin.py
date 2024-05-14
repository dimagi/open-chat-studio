from django.contrib import admin

from .models import Pipeline, PipelineRun


class PipelineRunInline(admin.TabularInline):
    model = PipelineRun
    extra = 0


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    inlines = [PipelineRunInline]
