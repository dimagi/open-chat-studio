class OriginDetectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if "chatbots" in request.path:
            request.origin = "chatbots"
        elif "experiments" in request.path:
            request.origin = "experiments"
        else:
            request.origin = None

        response = self.get_response(request)
        return response
