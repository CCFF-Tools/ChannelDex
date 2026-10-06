"""Resolve the station selected for the current browser session."""
from urllib.parse import urlparse

from django.shortcuts import redirect

from .models import Station

SESSION_KEY = "active_station_id"


def resolve_active_station(request):
    """Return the session station, with deterministic legacy fallbacks."""
    session = getattr(request, "session", {})
    station_id = session.get(SESSION_KEY)
    if station_id:
        station = Station.objects.filter(pk=station_id).first()
        if station:
            return station
        if hasattr(session, "pop"):
            session.pop(SESSION_KEY, None)
    station = Station.objects.filter(name="PUB-TV").order_by("pk").first()
    return station or Station.objects.order_by("pk").first()


def _validated_local_path(request, value):
    parsed = urlparse(value or "")
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return path


def local_next_url(request, fallback="dashboard"):
    """Return a same-origin relative referrer, or a named fallback."""
    referer = request.META.get("HTTP_REFERER", "")
    parsed = urlparse(referer)
    return _validated_local_path(request, referer) or "/"


def switch_station(request):
    station_id = request.POST.get("station")
    station = Station.objects.filter(pk=station_id).first()
    if station is None:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("Invalid station")
    request.session[SESSION_KEY] = station.pk
    next_url = _validated_local_path(request, request.POST.get("next")) or local_next_url(request)
    return redirect(next_url)
