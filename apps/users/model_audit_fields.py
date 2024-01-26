from django.contrib.auth.models import AbstractUser

# The auditing library struggles with dates. Let's ignore them for now
CUSTOM_USER_FIELDS = [f.attname for f in AbstractUser._meta.fields if f.attname not in ["last_login", "date_joined"]]
