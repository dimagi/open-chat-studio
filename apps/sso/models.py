from django.contrib.sessions.models import Session
from django.db import models


class SsoSession(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    django_session = models.ForeignKey(Session, on_delete=models.CASCADE)
    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE)

    def __str__(self):
        return self.id
