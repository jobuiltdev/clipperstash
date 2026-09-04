from django.urls import path

from apps.twitch import views

app_name = "twitch"

urlpatterns = [
    path("oauth/start/", views.oauth_start, name="oauth-start"),
    path("oauth/callback/", views.oauth_callback, name="oauth-callback"),
    path("connection/", views.connection_status, name="connection"),
]
