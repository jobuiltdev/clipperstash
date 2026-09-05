from django.contrib import admin
from django.urls import include, path

from clipperstash.health import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/streamers/", include("apps.streamers.urls")),
    path("api/twitch/", include("apps.twitch.urls")),
    # Read-only dashboard routes. Listed last so the app-specific prefixes
    # above always win; nothing here shadows them.
    path("api/", include("apps.dashboard.urls")),
]
