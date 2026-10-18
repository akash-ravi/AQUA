import logging

import httpx

log = logging.getLogger("jarvis.elevenlabs")

URL = "https://api.elevenlabs.io/v1/single-use-token/"
TIMEOUT_SECONDS = 5
# What the phone opens with each token: realtime speech-to-text, or the text-to-speech WebSocket.
TOKEN_TYPES = {"listen": "realtime_scribe", "speak": "ttd_websocket"}


class ElevenLabs:
    """Mints single-use tokens (15 minutes, one connection) so the phone streams to ElevenLabs without the key."""

    def __init__(self, api_key: str):
        # One client keeps the TLS connection warm, so a token costs one round trip.
        self.client = httpx.AsyncClient(headers={"xi-api-key": api_key}, timeout=TIMEOUT_SECONDS)

    async def token(self, kind: str) -> str | None:
        try:
            response = await self.client.post(URL + TOKEN_TYPES[kind])
            response.raise_for_status()
            return response.json()["token"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            log.info("ElevenLabs %s token failed: %s", kind, error)
            return None
