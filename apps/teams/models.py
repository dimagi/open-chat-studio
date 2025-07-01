import uuid

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext
from field_audit import audit_fields
from field_audit.models import AuditingManager
from waffle import get_setting
from waffle.managers import FlagManager
from waffle.models import CACHE_EMPTY, AbstractUserFlag
from waffle.utils import get_cache, keyfmt

from apps.teams import model_audit_fields
from apps.utils.models import BaseModel
from apps.web.meta import absolute_url


class TeamObjectManager(AuditingManager):
    pass


class MembershipObjectManager(AuditingManager):
    pass


@audit_fields(*model_audit_fields.TEAM_FIELDS, audit_special_queryset_writes=True)
class Team(BaseModel):
    """
    A team, with members.
    """

    objects = TeamObjectManager()
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="teams", through="Membership")

    def save(self, *args, **kwargs):
        from .helpers import get_next_unique_team_slug

        if not self.slug:
            self.slug = get_next_unique_team_slug(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def sorted_memberships(self):
        return self.membership_set.order_by("user__email")

    def pending_invitations(self):
        return self.invitations.filter(is_accepted=False)

    @property
    def dashboard_url(self) -> str:
        return reverse("web_team:home", args=[self.slug])


class PermissionsMixin(models.Model):
    groups = models.ManyToManyField(
        Group,
        verbose_name="Groups",
        blank=True,
        help_text=(
            "The groups this membership belongs to. A membership  will get all permissions "
            "granted to each of their groups."
        ),
        related_name="membership_set",
        related_query_name="membership",
    )

    class Meta:
        abstract = True

    def _get_permissions(self):
        perm_cache_name = "_perm_cache"
        if not hasattr(self, perm_cache_name):
            perms = (
                Permission.objects.filter(group__membership=self)
                .values_list("content_type__app_label", "codename")
                .order_by()
            )
            setattr(self, perm_cache_name, {f"{ct}.{name}" for ct, name in perms})
        return getattr(self, perm_cache_name)

    def has_perm(self, perm):
        return perm in self._get_permissions()

    def has_perms(self, perm_list):
        return all(self.has_perm(perm) for perm in perm_list)


@audit_fields(*model_audit_fields.MEMBERSHIP_FIELDS, audit_special_queryset_writes=True)
class Membership(BaseModel, PermissionsMixin):
    """
    A user's team membership
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    objects = MembershipObjectManager()

    def __str__(self):
        return f"{self.user}: {self.team}"

    def is_team_admin(self) -> bool:
        return self.has_perms(["teams.change_team", "teams.delete_team"])


class Invitation(BaseModel):
    """
    An invitation for new team members.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_invitations")
    is_accepted = models.BooleanField(default=False)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accepted_invitations", null=True, blank=True
    )
    groups = models.ManyToManyField(
        Group,
        verbose_name="Groups",
        blank=True,
        help_text="The groups to assign to the user when they accept the invitation.",
    )

    def get_url(self) -> str:
        return absolute_url(reverse("teams:accept_invitation", args=[self.id]))


class BaseTeamModel(BaseModel):
    """
    Abstract model for objects that are part of a team.
    """

    team = models.ForeignKey(Team, verbose_name=gettext("Team"), on_delete=models.CASCADE)

    class Meta:
        abstract = True


class FlagObjectManager(FlagManager, AuditingManager):
    pass


@audit_fields(*model_audit_fields.FLAG_FIELDS, audit_special_queryset_writes=True)
class Flag(AbstractUserFlag):
    """Custom Waffle flag to support usage with teams.

    See https://waffle.readthedocs.io/en/stable/types/flag.html#custom-flag-models"""

    FLAG_TEAMS_CACHE_KEY = "FLAG_TEAMS_CACHE_KEY"
    FLAG_TEAMS_CACHE_KEY_DEFAULT = "flag:%s:teams"

    teams = models.ManyToManyField(
        Team,
        blank=True,
        help_text=gettext("Activate this flag for these teams."),
    )
    objects = FlagObjectManager()

    def get_flush_keys(self, flush_keys=None):
        flush_keys = super().get_flush_keys(flush_keys)
        teams_cache_key = get_setting(Flag.FLAG_TEAMS_CACHE_KEY, Flag.FLAG_TEAMS_CACHE_KEY_DEFAULT)
        flush_keys.append(keyfmt(teams_cache_key, self.name))
        return flush_keys

    def is_active(self, request, read_only=False):
        is_active = super().is_active(request, read_only)
        if is_active:
            return is_active

        if not self.pk:
            # flag not created
            return False

        team = request and getattr(request, "team", None)
        return self.is_active_for_team(team)

    def is_active_for_team(self, team):
        if not team:
            return False

        if not self.pk:
            if get_setting("CREATE_MISSING_FLAGS"):
                flag, _created = Flag.objects.get_or_create(
                    name=self.name, defaults={"everyone": get_setting("FLAG_DEFAULT")}
                )
                self.id = flag.id
                self.refresh_from_db()
                cache = get_cache()
                cache.set(self._cache_key(self.name), self)

            return get_setting("FLAG_DEFAULT")

        team_ids = self._get_team_ids()
        return team.pk in team_ids

    def _get_team_ids(self):
        cache = get_cache()
        cache_key = keyfmt(get_setting(Flag.FLAG_TEAMS_CACHE_KEY, Flag.FLAG_TEAMS_CACHE_KEY_DEFAULT), self.name)
        cached = cache.get(cache_key)
        if cached == CACHE_EMPTY:
            return set()
        if cached:
            return cached

        team_ids = set(self.teams.all().values_list("pk", flat=True))
        cache.add(cache_key, team_ids or CACHE_EMPTY)
        return team_ids

    def clean(self):
        # Custom validation logic to enforce naming convention
        if not self.name.startswith("flag_"):
            raise ValidationError(f"Flag name must start with 'flag_': {self.name}")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
