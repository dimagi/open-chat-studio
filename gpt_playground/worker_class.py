from uvicorn.workers import UvicornWorker


class DjangoUvicornWorker(UvicornWorker):
    """
    See https://stackoverflow.com/questions/75217343/django-can-only-handle-asgi-http-connections-not-lifespan-in-uvicorn
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config.lifespan = "off"
