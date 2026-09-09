from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.contrib.staticfiles import finders


class StaticFilesMiddleware:
    """Small dependency-free fallback for environments before WhiteNoise install."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        prefix = settings.STATIC_URL.rstrip("/") + "/"
        if request.path.startswith(prefix):
            relative = request.path[len(prefix):]
            found = finders.find(relative)
            if found:
                path = Path(found)
                if path.is_file():
                    return FileResponse(path.open("rb"))
            raise Http404
        return self.get_response(request)
