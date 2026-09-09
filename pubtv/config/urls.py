from django.urls import path
from pubtv.operations import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("shows/", views.show_list, name="show-list"),
    path("shows/<int:show_id>/", views.show_detail, name="show-detail"),
    path("occurrences/new/", views.occurrence_create, name="occurrence-create"),
    path("occurrences/<int:pk>/edit/", views.occurrence_edit, name="occurrence-edit"),
    path("setup/station/", views.setup_station, name="setup-station"), path("setup/device/", views.setup_device, name="setup-device"),
    path("shows/new/", views.show_create, name="show-create"), path("episodes/new/", views.episode_create, name="episode-create"),
    path("assets/new/", views.asset_create, name="asset-create"), path("deliveries/new/", views.delivery_create, name="delivery-create"),
    path("slots/new/", views.slot_create, name="slot-create"), path("assignments/new/", views.assignment_create, name="assignment-create"),
    path("day/", views.day_view, name="day-view"), path("week/", views.week_view, name="week-view"), path("agenda/", views.agenda_view, name="agenda"), path("history/", views.history_view, name="history"),
    path("occurrences/<int:pk>/preparation/", views.preparation_edit, name="preparation-edit"), path("occurrences/<int:pk>/programming/", views.programming_create, name="programming-create"),
    path("uploads/new/", views.upload_create, name="upload-create"), path("occurrences/<int:pk>/airing/", views.airing_create, name="airing-create"),
    path("uploads/<int:pk>/<str:state>/", views.upload_transition, name="upload-transition"),
]
