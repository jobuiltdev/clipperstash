from django.urls import path

from apps.monitoring import views as monitoring_views
from apps.streamers import views

app_name = "streamers"

urlpatterns = [
    path("resolve/", views.resolve_streamer, name="resolve"),
    # Streamer-scoped in the URL, but the behavior belongs to the monitoring app.
    path(
        "<int:streamer_id>/observe/",
        monitoring_views.observe_streamer,
        name="observe",
    ),
]
