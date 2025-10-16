import json

from django.db import models, transaction
from django.http import JsonResponse
from django.views import View
from django.views.decorators.http import require_http_methods

from apps.filters.models import FilterSet
from apps.filters.serializers import FilterSetCreateUpdateSerializer
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


def _to_dict(fs: FilterSet) -> dict:
    return {
        "id": fs.id,
        "name": fs.name,
        "table_type": fs.table_type,
        "filter_query_string": fs.filter_query_string,
        "is_shared": fs.is_shared,
        "is_starred": fs.is_starred,
        "is_default_for_user": fs.is_default_for_user,
        "is_default_for_team": fs.is_default_for_team,
    }


@require_http_methods(["GET"])
@login_and_team_required
def list_filter_sets(request, team_slug: str, table_type: str):
    # Validate table_type against allowed choices
    if not FilterSet.is_valid_table_type(table_type):
        return JsonResponse({"error": "Invalid table_type"}, status=400)

    qs = (
        FilterSet.objects.filter(
            team=request.team,
            table_type=table_type,
        )
        .filter(models.Q(user=request.user) | models.Q(is_shared=True))
        .order_by("-is_starred", "name")
    )
    data = [_to_dict(fs) for fs in qs.all()]
    return JsonResponse({"results": data})


@require_http_methods(["POST"])
@login_and_team_required
def create_filter_set(request, team_slug: str, table_type: str):
    # Validate table_type against allowed choices
    if not FilterSet.is_valid_table_type(table_type):
        return JsonResponse({"error": "Invalid table_type"}, status=400)

    data = {"name": request.POST.get("name"), "filter_query_string": request.POST.get("filter_query_string")}
    serializer = FilterSetCreateUpdateSerializer(
        data=data, context={"is_team_admin": request.team_membership.is_team_admin}
    )
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    validated = serializer.validated_data

    with transaction.atomic():
        if validated.get("is_default_for_user"):
            FilterSet.objects.filter(
                team=request.team, user=request.user, table_type=table_type, is_default_for_user=True
            ).update(is_default_for_user=False)
        if validated.get("is_default_for_team"):
            FilterSet.objects.filter(team=request.team, table_type=table_type, is_default_for_team=True).update(
                is_default_for_team=False
            )
        filter_set = FilterSet.objects.create(
            team=request.team,
            user=request.user,
            name=validated.get("name", "").strip(),
            table_type=table_type,
            filter_query_string=validated.get("filter_query_string", ""),
            is_shared=validated.get("is_shared", False),
            is_starred=validated.get("is_starred", False),
            is_default_for_user=validated.get("is_default_for_user", False),
            is_default_for_team=validated.get("is_default_for_team", False),
        )
    return JsonResponse({"success": True, "filter_set": _to_dict(filter_set)})


class FilterSetView(LoginAndTeamRequiredMixin, View):
    """Handle PATCH (edit) and DELETE operations for FilterSet objects."""

    def get_object(self, request, pk):
        """Get the FilterSet object or return None if not found."""
        try:
            return FilterSet.objects.get(team=request.team, id=pk)
        except FilterSet.DoesNotExist:
            return None

    def patch(self, request, team_slug: str, pk: int):
        """Handle PATCH request to edit a filter set."""
        fs = self.get_object(request, pk)
        if not fs:
            return JsonResponse({"error": "Not found"}, status=404)

        payload = json.loads(request.body or b"{}")
        serializer = FilterSetCreateUpdateSerializer(
            data=payload, partial=True, context={"is_team_admin": request.team_membership.is_team_admin}
        )
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)
        validated = serializer.validated_data

        with transaction.atomic():
            updates = []
            if "filter_query_string" in validated:
                fs.filter_query_string = validated["filter_query_string"]
                updates.append("filter_query_string")
            if "is_shared" in validated:
                fs.is_shared = bool(validated["is_shared"])
                updates.append("is_shared")
            if "is_starred" in validated:
                fs.is_starred = bool(validated["is_starred"])
                updates.append("is_starred")

            if validated.get("is_default_for_user") is True:
                FilterSet.objects.filter(
                    team=request.team, user=request.user, table_type=fs.table_type, is_default_for_user=True
                ).exclude(id=fs.id).update(is_default_for_user=False)
                fs.is_default_for_user = True
                updates.append("is_default_for_user")
            elif validated.get("is_default_for_user") is False:
                fs.is_default_for_user = False
                updates.append("is_default_for_user")

            if validated.get("is_default_for_team") is True:
                FilterSet.objects.filter(team=request.team, table_type=fs.table_type, is_default_for_team=True).exclude(
                    id=fs.id
                ).update(is_default_for_team=False)
                fs.is_default_for_team = True
                updates.append("is_default_for_team")
            elif validated.get("is_default_for_team") is False:
                fs.is_default_for_team = False
                updates.append("is_default_for_team")

            if updates:
                fs.save(update_fields=updates)

        return JsonResponse({"result": _to_dict(fs)})

    def delete(self, request, team_slug: str, pk: int):
        """Handle DELETE request to delete a filter set."""
        fs = self.get_object(request, pk)
        if not fs:
            return JsonResponse({"error": "Not found"}, status=404)

        is_owner = fs.user == request.user
        is_team_admin = request.team_membership.is_team_admin

        # Only owner or team admin can delete
        if not (is_owner or is_team_admin):
            return JsonResponse({"error": "You don't have permission to delete this filter set"}, status=403)

        fs.delete()
        return JsonResponse({"success": True})
