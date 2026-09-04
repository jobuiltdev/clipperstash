from django.apps import apps
from django.conf import settings
from django.urls import reverse

from clipperstash.celery import app as celery_app

PRODUCT_APPS = [
    "apps.streamers",
    "apps.twitch",
    "apps.monitoring",
    "apps.moments",
    "apps.clips",
]


def test_product_apps_are_installed() -> None:
    installed = {config.name for config in apps.get_app_configs()}

    assert set(PRODUCT_APPS).issubset(installed)


def test_rest_framework_is_configured() -> None:
    assert "rest_framework" in settings.INSTALLED_APPS
    assert settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] == [
        "rest_framework.renderers.JSONRenderer"
    ]


def test_database_uses_postgresql() -> None:
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


def test_celery_uses_the_configured_broker() -> None:
    assert celery_app.conf.broker_url == settings.CELERY_BROKER_URL
    assert celery_app.conf.result_backend == settings.CELERY_RESULT_BACKEND
    assert "clipperstash.ping" in celery_app.tasks


def test_health_route_is_registered() -> None:
    assert reverse("health") == "/api/health/"


def test_no_recurring_schedule_is_registered() -> None:
    """V0 runs no periodic work.

    In particular, Twitch's requirement to validate an OAuth access token at
    startup and hourly thereafter is documented as a deferred runtime
    obligation, not automated here. This test fails if a schedule is added
    without revisiting that documentation.
    """
    assert not celery_app.conf.beat_schedule
