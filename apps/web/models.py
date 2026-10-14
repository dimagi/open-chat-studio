from django.conf import settings
from django.db import models


class SuperuserElevation(models.Model):
    """One privilege elevation, written when it is granted and stamped if it is released early.

    Expiry is not written: an elevation that was never released ended at `expires_at`.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    grant = models.CharField(max_length=64)
    granted_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "grant", "expires_at"])]

    def __str__(self):
        return f"{self.user_id} → {self.grant} at {self.granted_at:%Y-%m-%d %H:%M}"
