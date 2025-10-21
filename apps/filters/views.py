import json

from django.db import IntegrityError, models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.decorators.http import require_http_methods

from apps.filters.models import FilterSet
from apps.filters.serializers import FilterSetSerializer
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


@require_http_methods(["GET"])
@login_and_team_required
def list_filter_sets(request, team_slug: str, table_type: str):
    # Validate table_type against allowed choices
    if not FilterSet.is_valid_table_type(table_type):
        return JsonResponse({"error": "Invalid table_type"}, status=400)

    queryset = (
        FilterSet.objects.filter(
            team=request.team,
            table_type=table_type,
        )
        .filter(models.Q(user=request.user) | models.Q(is_shared=True))
        .order_by("-is_starred", "name")
    )
    serializer = FilterSetSerializer(queryset, many=True, context={"request_user": request.user})
    return JsonResponse({"results": serializer.data})


@require_http_methods(["POST"])
@login_and_team_required
def create_filter_set(request, team_slug: str, table_type: str):
    # Validate table_type against allowed choices
    if not FilterSet.is_valid_table_type(table_type):
        return JsonResponse({"error": "Invalid table_type"}, status=400)

    data = {
        "name": request.POST.get("name"),
        "filter_query_string": request.POST.get("filter_query_string"),
        "table_type": table_type,
    }
    serializer = FilterSetSerializer(
        data=data, context={"is_team_admin": request.team_membership.is_team_admin, "request_user": request.user}
    )
    if not serializer.is_valid():
        print(serializer.errors)
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

        try:
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
            serializer = FilterSetSerializer(filter_set, context={"request_user": request.user})
            return JsonResponse({"success": True, "filter_set": serializer.data})
        except IntegrityError:
            return JsonResponse({"error": "Unable to create filter set"}, status=400)


class FilterSetView(LoginAndTeamRequiredMixin, View):
    """Handle PATCH (edit) and DELETE operations for FilterSet objects."""

    def patch(self, request, team_slug: str, pk: int):
        """Handle PATCH request to edit a filter set."""
        filter_set = get_object_or_404(FilterSet, team=request.team, id=pk, user=request.user)

        payload = json.loads(request.body or b"{}")
        serializer = FilterSetSerializer(
            data=payload,
            partial=True,
            context={"is_team_admin": request.team_membership.is_team_admin, "request_user": request.user},
        )
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)
        validated = serializer.validated_data

        with transaction.atomic():
            if validated.get("is_default_for_user") is True:
                FilterSet.objects.filter(
                    team=request.team, user=request.user, table_type=filter_set.table_type, is_default_for_user=True
                ).exclude(id=filter_set.id).update(is_default_for_user=False)

            if validated.get("is_default_for_team") is True:
                FilterSet.objects.filter(
                    team=request.team, table_type=filter_set.table_type, is_default_for_team=True
                ).exclude(id=filter_set.id).update(is_default_for_team=False)

            serializer.update(filter_set, validated)

        serializer = FilterSetSerializer(filter_set, context={"request_user": request.user})
        return JsonResponse({"result": serializer.data})

    def delete(self, request, team_slug: str, pk: int):
        """Handle DELETE request to delete a filter set."""
        filter_set = get_object_or_404(FilterSet, team=request.team, id=pk, user=request.user)
        filter_set.delete()
        return JsonResponse({"success": True})
