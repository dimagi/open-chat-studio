from django.contrib import admin

from .models import Pipeline


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    pass
