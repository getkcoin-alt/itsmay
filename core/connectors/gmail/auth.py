"""Gmail OAuth credential loader.

Reads a JSON credentials blob from `GOOGLE_CREDENTIALS_JSON` (produced once by
`scripts/gmail_auth.py`). Auto-refreshes expired access tokens in-place and
returns a `google.oauth2.credentials.Credentials` object ready to use with
`googleapiclient.discovery.build`.
"""

from __future__ import annotations

import json

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    _HAS_GOOGLE_AUTH = True
except ImportError:
    Request = None  # type: ignore[assignment,misc]
    Credentials = None  # type: ignore[assignment,misc]
    _HAS_GOOGLE_AUTH = False


def load_credentials(credentials_json: str):
    """Parse the stored JSON blob and refresh if the access token is expired."""
    if not _HAS_GOOGLE_AUTH:
        raise RuntimeError("google-auth is not installed — run: pip install -e '.[gmail]'")
    info = json.loads(credentials_json)
    creds = Credentials.from_authorized_user_info(info)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "Gmail credentials are invalid and cannot be refreshed. "
                "Re-run scripts/gmail_auth.py to get a fresh token."
            )
    return creds
