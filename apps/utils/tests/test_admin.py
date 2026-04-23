from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin
from apps.utils.tests.models import Bot


class TestReadonlyAdminMixin:
    def _build_admin(self, declared_readonly=()):
        class _BotAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
            readonly_fields = declared_readonly

            def computed_display(self, obj):
                return ""

        return _BotAdmin(Bot, admin.site)

    def test_add_view_returns_only_declared_readonly(self):
        bot_admin = self._build_admin(declared_readonly=["computed_display"])
        assert bot_admin.get_readonly_fields(request=None, obj=None) == ["computed_display"]

    def test_change_view_appends_model_fields_preserving_declared_order(self):
        bot_admin = self._build_admin(declared_readonly=["computed_display"])
        result = bot_admin.get_readonly_fields(request=None, obj=object())
        assert result[0] == "computed_display"
        assert set(result[1:]) == {"id", "name", "collection", "tools"}
        assert len(result) == len(set(result)), "duplicates present"

    def test_change_view_with_no_declared_readonly(self):
        bot_admin = self._build_admin(declared_readonly=())
        result = bot_admin.get_readonly_fields(request=None, obj=object())
        assert set(result) == {"id", "name", "collection", "tools"}

    def test_declared_model_field_is_not_duplicated(self):
        bot_admin = self._build_admin(declared_readonly=["name"])
        result = bot_admin.get_readonly_fields(request=None, obj=object())
        assert result.count("name") == 1
        assert result[0] == "name"
