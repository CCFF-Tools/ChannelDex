from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect

from .active_station import resolve_active_station, workspace_safe_url


class StationOriginGuardMiddleware:
    """Reject a form left open after another tab changes the workspace."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path != "/stations/switch/":
            origin = request.POST.get("originating_station")
            browser_request = bool(request.META.get("HTTP_SEC_FETCH_SITE"))
            if not origin and browser_request:
                from .views import bad_request
                return bad_request(request)
            if origin:
                station = resolve_active_station(request)
                origin_matches = origin == "none" and station is None
                if not origin_matches and (station is None or str(station.pk) != origin):
                    messages.error(
                        request,
                        "This form was opened in another working channel. Nothing was saved; review it again in the current channel.",
                    )
                    response = redirect(workspace_safe_url(request.path))
                    response.status_code = 303
                    return response
        return self.get_response(request)
