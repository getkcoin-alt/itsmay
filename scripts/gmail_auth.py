#!/usr/bin/env python3
"""One-time Gmail OAuth2 authorization flow.

Run this locally (not on Railway) to get the credentials JSON you need to
paste into your GOOGLE_CREDENTIALS_JSON env var.

Setup:
  1. Go to console.cloud.google.com → APIs & Services → OAuth 2.0 Client IDs
  2. Create a "Desktop app" credential and download the JSON file.
  3. Run:
       python scripts/gmail_auth.py --client-secrets /path/to/client_secret.json

The script opens a browser for consent, then prints the token JSON.
Copy the entire printed JSON string into:
  - Your local .env:   GOOGLE_CREDENTIALS_JSON='{ ... }'
  - Railway dashboard: GOOGLE_CREDENTIALS_JSON → paste the JSON

The refresh token inside is long-lived. Re-run only if you revoke access.
"""

from __future__ import annotations

import argparse
import json
import sys

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize Gmail and print credentials JSON.")
    parser.add_argument("--client-secrets", required=True, help="Path to client_secret_*.json")
    parser.add_argument(
        "--out",
        default=None,
        help="Write JSON to this file instead of stdout (optional).",
    )
    args = parser.parse_args()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "Error: google-auth-oauthlib not installed.\n"
            "Run: pip install google-auth-oauthlib",
            file=sys.stderr,
        )
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }
    token_json = json.dumps(token_data, indent=None)

    if args.out:
        with open(args.out, "w") as f:
            f.write(token_json)
        print(f"Credentials written to {args.out}")
        print("Set it as env var:")
        print(f"  GOOGLE_CREDENTIALS_JSON='{token_json}'")
    else:
        print("\n── COPY THIS INTO YOUR ENV ────────────────────────────────────────")
        print(f"GOOGLE_CREDENTIALS_JSON='{token_json}'")
        print("──────────────────────────────────────────────────────────────────\n")
        print(
            "On Railway: paste only the JSON value (without the outer quotes and var name) "
            "as the value for the GOOGLE_CREDENTIALS_JSON variable."
        )


if __name__ == "__main__":
    main()
