from pubtv.operations.active_station import resolve_active_station
from pubtv.operations.models import Station
from django.test.testcases import DatabaseOperationForbidden
from django.contrib import messages


def active_station(request):
    try:
        station = resolve_active_station(request)
        if getattr(request, "active_station_fallback", False):
            selected = station.name if station else "no working channel"
            messages.info(request, f"The previous working channel is unavailable; {selected} is selected.")
        return {
            "active_station": station,
            "available_stations": Station.objects.order_by("name", "pk"),
        }
    except DatabaseOperationForbidden:
        # SimpleTemplateTestCase documentation checks render templates without
        # a database; keep the shared base template usable there.
        return {"active_station": None, "available_stations": ()}
