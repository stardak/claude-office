import hmac
import re
from urllib.parse import urlparse

from fastapi import WebSocket

# ``ConnectionManager`` and the ``manager`` singleton live in the domain layer
# (``app.core.connection_manager``) so that ``core/`` and ``services/`` need not
# import from ``app.api`` (ARC-011). They are re-exported here for backward
# compatibility with transport-level consumers (``app.main``, route handlers).
from app.core.connection_manager import (  # noqa: F401
    ConnectionManager as ConnectionManager,
)
from app.core.connection_manager import (
    get_manager as get_manager,
)
from app.core.connection_manager import (
    manager as manager,
)
from app.core.connection_manager import (
    override_manager as override_manager,
)

# Valid session/room IDs: alphanumeric, dashes, underscores only
_VALID_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

# Loopback hosts permitted for browser WebSocket handshakes. The allowlist is
# derived from ``settings.BACKEND_CORS_ORIGINS`` at call time but filtered to
# these hosts so a CORS-config change can never widen the WS trust boundary
# beyond the local machine (QA-014).
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _allowed_ws_origins() -> frozenset[str]:
    """Origins permitted for WebSocket connections, derived from settings.

    Filtered to loopback hosts so a CORS-config change can never open the
    WebSocket stream to non-local origins.
    """
    from app.config import get_settings

    settings = get_settings()
    local = {
        origin.rstrip("/")
        for origin in settings.BACKEND_CORS_ORIGINS
        if urlparse(origin).hostname in _LOCALHOST_HOSTS
    }
    return frozenset(local | {o.rstrip("/") for o in settings.PUBLIC_ORIGINS})


def validate_websocket_origin(websocket: WebSocket) -> bool:
    """Check the Origin header on a WebSocket handshake.

    Browser connections must come from an allowed localhost origin.
    Non-browser connections (no Origin header) must present a valid
    X-API-Key matching the *effective* API key (either user-configured
    or the per-launch auto-generated token).  This prevents arbitrary
    local processes from subscribing to the session-state stream when
    no explicit key is configured.
    """
    origin = websocket.headers.get("origin")
    if origin is not None:
        return origin.rstrip("/") in _allowed_ws_origins()

    # Non-browser clients (no Origin) — always require the effective API key
    from app.config import get_settings

    key = get_settings().effective_api_key
    provided = websocket.headers.get("x-api-key", "")
    return hmac.compare_digest(provided, key)


def validate_session_id(session_id: str) -> bool:
    """Return True if *session_id* matches the expected format."""
    return bool(_VALID_ID_PATTERN.match(session_id))
