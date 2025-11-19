from oauth2_provider.models import AbstractApplication, ApplicationManager


class OAuth2Application(AbstractApplication):
    # Custom application model can be extended here if needed
    objects = ApplicationManager()
