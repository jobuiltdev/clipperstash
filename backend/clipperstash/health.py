"""Service health endpoint.

The response is intentionally a fixed, static payload. It must never leak
environment variables, credentials, connection strings, versions or any other
internal configuration detail.
"""

from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response


@api_view(["GET"])
def health(_request: Request) -> Response:
    """Return a minimal liveness signal for the backend."""
    return Response({"status": "ok"})
