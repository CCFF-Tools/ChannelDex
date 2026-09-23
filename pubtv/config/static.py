from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.http import FileResponse, Http404


class DevelopmentManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """Use cache-busting names after collection, but tolerate uncollected dev assets."""

    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            return name


class StaticFilesMiddleware:
    """Small dependency-free fallback for environments before WhiteNoise install."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        prefix = settings.STATIC_URL.rstrip("/") + "/"
        if request.path.startswith(prefix):
            relative = request.path[len(prefix):]
            collected = Path(settings.STATIC_ROOT) / relative
            found = collected if collected.is_file() else finders.find(relative)
            if found:
                path = Path(found)
                if path.is_file():
                    return FileResponse(path.open("rb"))
            raise Http404
        return self.get_response(request)
