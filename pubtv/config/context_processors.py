from pubtv.operations.active_station import resolve_active_station
from pubtv.operations.models import Station
from django.test.testcases import DatabaseOperationForbidden


def active_station(request):
    try:
        return {
            "active_station": resolve_active_station(request),
            "available_stations": Station.objects.order_by("name", "pk"),
        }
    except DatabaseOperationForbidden:
        # SimpleTemplateTestCase documentation checks render templates without
        # a database; keep the shared base template usable there.
        return {"active_station": None, "available_stations": ()}
