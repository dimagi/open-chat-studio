from .settings import *  # noqa F401
from socket import gethostbyname, gethostname

DEBUG = False

# Django security checklist settings.
# More details here: https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HTTP Strict Transport Security settings
# Without uncommenting the lines below, you will get security warnings when running ./manage.py check --deploy
# https://docs.djangoproject.com/en/3.2/ref/middleware/#http-strict-transport-security

# # Increase this number once you're confident everything works https://stackoverflow.com/a/49168623/8207
SECURE_HSTS_SECONDS = 60
# # Uncomment these two lines if you are sure that you don't host any subdomains over HTTP.
# # You will get security warnings if you don't do this.
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Be 100% sure that all assets from this and all subdomains can be loaded over HTTPS before enabling preloading.
# Also see the submission requirements: https://hstspreload.org/#submission-requirements
# SECURE_HSTS_PRELOAD = True

USE_HTTPS_IN_ABSOLUTE_URLS = True

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
# Add the server's own hostname to ALLOWED_HOSTS
ALLOWED_HOSTS.append(gethostbyname(gethostname()))

# this defaults to SECRET_KEY
CRYPTOGRAPHY_KEY = env("CRYPTOGRAPHY_KEY", default=None)
CRYPTOGRAPHY_SALT = env("CRYPTOGRAPHY_SALT", default=None)

# This is true by default, but let's be explicit
SECURE_CONTENT_TYPE_NOSNIFF = True

# Your email config goes here.
# see https://github.com/anymail/django-anymail for more details / examples
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", default="anymail.backends.mailgun.EmailBackend")
match EMAIL_BACKEND:
    case "anymail.backends.mailgun.EmailBackend":
        ANYMAIL = {
            "MAILGUN_API_KEY": env("MAILGUN_API_KEY", default=None),
            "MAILGUN_SENDER_DOMAIN": env("MAILGUN_SENDER_DOMAIN", default=None),
        }
    case "anymail.backends.amazon_ses.EmailBackend":
        ses_params = {
            "aws_access_key_id": env("AWS_SES_ACCESS_KEY", default=None),
            "aws_secret_access_key": env("AWS_SES_SECRET_KEY", default=None),
            "region_name": env("AWS_SES_REGION", default=None),
        }
        ANYMAIL = {
            "AMAZON_SES_CLIENT_PARAMS": dict(item for item in ses_params.items() if item[1]),
        }
    case _:
        raise Exception(f"Unknown email backend: {EMAIL_BACKEND}")

SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", default="noreply@dimagi.com")
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="noreply@dimagi.com")

# Mailchimp setup

# set these values if you want to subscribe people to a mailchimp list after they sign up.
MAILCHIMP_API_KEY = env("MAILCHIMP_API_KEY", default=None)
MAILCHIMP_LIST_ID = env("MAILCHIMP_LIST_ID", default=None)

# Allow unacknowledged tasks to be rescheduled after 5 minutes
# (the default is 1 hour). If this number is too low, a task may
# get executed more than once at a time. The higher the number,
# the longer it will take for a lost task to get rescheduled.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 60 * 5}
# Reschedule un-acked tasks on worker failure (ie SIGKILL)
CELERY_REJECT_ON_WORKER_LOST = True
