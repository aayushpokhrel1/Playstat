"""API-key authentication for the Playstat API.

Config (parsed once at import/startup):
- AUTH_ENABLED: "true"/"1"/"yes" enables auth; anything else (or unset)
  disables it entirely — one env flip reverts to the old open behavior.
- PLAYSTAT_API_KEYS: comma-separated "name:key" pairs, e.g.
  "dashboard:abc123,budgerr:def456". Names exist only for provisioning
  bookkeeping (per-consumer revocation); they are never logged or echoed.

Clients send the key in the `X-API-Key` header.
"""

import os
import secrets

from fastapi import HTTPException, Request

AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "").strip().lower() in ("true", "1", "yes")


def _parse_keys(raw: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, sep, key = pair.partition(":")
        if sep and name.strip() and key.strip():
            keys[name.strip()] = key.strip()
    return keys


API_KEYS = _parse_keys(os.environ.get("PLAYSTAT_API_KEYS", ""))


def require_api_key(request: Request) -> None:
    """Global FastAPI dependency: reject requests without a valid X-API-Key.

    No-op when AUTH_ENABLED is false. Never includes the presented key (or
    any configured key) in errors or logs.
    """
    if not AUTH_ENABLED:
        return
    presented = request.headers.get("X-API-Key", "")
    # Compare against every configured key (constant-time per comparison)
    # rather than short-circuiting on a dict lookup keyed by the secret.
    valid = False
    for key in API_KEYS.values():
        if secrets.compare_digest(presented, key):
            valid = True
    if not valid:
        raise HTTPException(status_code=401, detail="missing or invalid API key")
