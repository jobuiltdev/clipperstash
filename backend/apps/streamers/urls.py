from django.urls import path

from apps.streamers import views

app_name = "streamers"

urlpatterns = [
    path("resolve/", views.resolve_streamer, name="resolve"),
]
