"""Resolve the station selected for the current browser session."""
from urllib.parse import parse_qs, urlencode, urlparse
import re

from django.shortcuts import redirect
from django.urls import Resolver404, resolve

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
        setattr(request, "active_station_fallback", True)
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
    # Object URLs carry the old workspace's primary key.  Keep neutral
    # calendar context, but return to a collection in the newly selected
    # workspace rather than rendering a misleading 404.
    return redirect(workspace_safe_url(next_url))


def workspace_safe_url(value, fallback="/"):
    """Collapse workspace-owned object URLs to their collection."""
    next_url = value or fallback
    parsed = urlparse(next_url)
    collection = None
    if re.fullmatch(r"/shows/\d+/(edit/|premieres/|slots/|slots/length/)?", parsed.path):
        collection = "/schedule/" if any(parse_qs(parsed.query).get(key) for key in ("date", "mode")) else "/shows/"
    elif re.fullmatch(r"/episodes/\d+/(edit/|duplicate/|workflow/new/)?", parsed.path): collection = "/episodes/"
    elif re.fullmatch(r"/settings/devices/\d+/(edit/)?", parsed.path): collection = "/settings/devices/"
    elif re.fullmatch(r"/occurrences/\d+/(edit/|preparation/|programming/|airing/)?", parsed.path): collection = "/schedule/"
    elif re.fullmatch(r"/slots/\d+/edit/", parsed.path): collection = "/schedule/recurring/"
    elif re.fullmatch(r"/assets/\d+/(edit/|transfers/new/|transfers/\d+/edit/)", parsed.path): collection = "/media/library/"
    elif re.fullmatch(r"/media/intake/saved/\d+/", parsed.path): collection = "/media/library/"
    elif parsed.path.startswith("/media/assets/"): collection = "/media/library/"
    elif parsed.path == "/media/preparation/": collection = "/media/preparation/"
    elif parsed.path == "/schedule/guided-delivery/": collection = "/schedule/guided-delivery/"
    elif parsed.path in {"/schedule/review/", "/schedule/prepare/"}: collection = parsed.path
    elif parsed.path in {"/assets/new/", "/episodes/new/", "/occurrences/new/"}: collection = parsed.path
    elif parsed.path in {"/media/", "/media/library/"}: collection = parsed.path
    if collection:
        query = parse_qs(parsed.query, keep_blank_values=True)
        allowed = ({"date", "mode", "days", "view"} if collection == "/schedule/" else
                   {"readiness", "q"} if collection in {"/media/", "/media/library/"} else
                   {"scope"} if collection == "/media/preparation/" else
                   {"date"} if collection in {"/schedule/review/", "/schedule/prepare/"} else set())
        keep = {key: value[-1] for key, value in query.items() if key in allowed and value}
        next_url = collection
        if keep:
            next_url += "?" + urlencode(keep)
    else:
        try:
            resolve(parsed.path)
        except Resolver404:
            next_url = fallback
        else:
            # Collection filters may survive a workspace switch only when they
            # cannot identify an object from the previous workspace.
            safe_queries = {
                "/schedule/": {"date", "mode", "days", "view"},
                "/day/": {"date"}, "/week/": {"date"}, "/agenda/": {"date", "days"},
                "/shows/": {"q", "sort", "show_type"},
                "/media/library/": {"readiness", "q"},
                "/media/preparation/": {"scope"},
                "/schedule/review/": {"date"},
                "/schedule/prepare/": {"date"},
            }
            query = parse_qs(parsed.query, keep_blank_values=True)
            keep = {key: values[-1] for key, values in query.items()
                    if key in safe_queries.get(parsed.path, set()) and values}
            next_url = parsed.path
            if keep:
                next_url += "?" + urlencode(keep)
    return next_url
