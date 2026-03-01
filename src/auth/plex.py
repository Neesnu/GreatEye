from dataclasses import dataclass

import httpx
import structlog

from src.config import settings

logger = structlog.get_logger()

PLEX_PINS_URL = "https://plex.tv/api/v2/pins"
PLEX_USER_URL = "https://plex.tv/api/v2/user"
PLEX_AUTH_URL = "https://app.plex.tv/auth#!"

PLEX_HEADERS = {
    "Accept": "application/json",
    "X-Plex-Product": "Great Eye",
    "X-Plex-Version": "1.0.0",
    "X-Plex-Client-Identifier": "",  # Set at runtime
}


@dataclass
class PlexPin:
    pin_id: int
    code: str


@dataclass
class PlexUser:
    username: str
    plex_user_id: str
    auth_token: str
    email: str | None = None
    thumb: str | None = None


def _headers() -> dict[str, str]:
    """Build Plex API headers with client ID."""
    h = dict(PLEX_HEADERS)
    h["X-Plex-Client-Identifier"] = settings.plex_client_id
    return h


async def create_pin() -> PlexPin:
    """Request a new PIN from Plex for the OAuth flow."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            PLEX_PINS_URL,
            headers=_headers(),
            data={"strong": "true"},
        )
        response.raise_for_status()
        data = response.json()
        return PlexPin(pin_id=data["id"], code=data["code"])


def get_auth_url(pin: PlexPin, callback_url: str) -> str:
    """Build the Plex auth URL for the user to authenticate."""
    return (
        f"{PLEX_AUTH_URL}"
        f"?clientID={settings.plex_client_id}"
        f"&code={pin.code}"
        f"&forwardUrl={callback_url}"
    )


async def check_pin(pin_id: int) -> str | None:
    """Check if a PIN has been claimed. Returns auth token or None."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PLEX_PINS_URL}/{pin_id}",
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("authToken")
        return token if token else None


async def get_plex_user(auth_token: str) -> PlexUser:
    """Fetch user info from Plex using an auth token."""
    headers = _headers()
    headers["X-Plex-Token"] = auth_token
    async with httpx.AsyncClient() as client:
        response = await client.get(PLEX_USER_URL, headers=headers)
        response.raise_for_status()
        data = response.json()
        return PlexUser(
            username=data["username"],
            plex_user_id=str(data["id"]),
            auth_token=auth_token,
            email=data.get("email"),
            thumb=data.get("thumb"),
        )
