from django.middleware.csrf import get_token


class CsrfCookieMiddleware:
    """Issue a CSRF cookie for the JavaScript client on page visits."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET":
            get_token(request)
        return self.get_response(request)
