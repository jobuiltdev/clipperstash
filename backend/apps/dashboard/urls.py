"""Dashboard routes.

Read-only by construction: every view accepts `GET` alone, so a `POST` to any
of these paths is refused by DRF before it reaches application code.
"""

from django.urls import path

from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("dashboard/overview/", views.overview, name="overview"),
    path("dashboard/detector-config/", views.detector_config, name="detector-config"),
    path("sessions/<int:session_id>/", views.session_detail, name="session-detail"),
    path("sessions/<int:session_id>/moments/", views.session_moments, name="session-moments"),
    path("moments/<int:moment_id>/", views.moment_detail, name="moment-detail"),
]
