"""One-time import of an existing Google token file into the database.

    python -m app.auth.import_token [PATH] [--replace]

PATH defaults to GOOGLE_TOKEN_FILE (or credentials/google_token.json). Needs
DATABASE_URL, GOOGLE_TOKEN_ENCRYPTION_KEY and GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET in the environment or .env — the same values the
deployed backend uses. Credentials already stored in the database are kept
unless --replace is given. The token file itself is never modified or
deleted. Prints no token values.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.auth import token_store
from app.auth.google_oauth import (
    GoogleOAuthError,
    _credentials_from_info,
    _credentials_to_info,
)
from app.config import GOOGLE_TOKEN_FILE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(GOOGLE_TOKEN_FILE))
    parser.add_argument(
        "--replace", action="store_true",
        help="overwrite credentials already stored in the database",
    )
    args = parser.parse_args(argv)
    path = Path(args.path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read a JSON token file at {path} ({type(exc).__name__}).")
        return 1
    if not isinstance(data, dict) or "web" in data or "installed" in data:
        print(
            f"{path} is not an authorized-user token (it looks like the OAuth "
            "client secret file). Nothing was imported."
        )
        return 1
    try:
        # Validates refresh token presence and that the token was issued to
        # the configured GOOGLE_CLIENT_ID.
        credentials = _credentials_from_info(data)
    except GoogleOAuthError as exc:
        print(f"{exc} Nothing was imported.")
        return 1

    info = _credentials_to_info(credentials)
    try:
        if args.replace:
            version = token_store.save(info)
        else:
            version = token_store.save_if_absent(info)
    except token_store.TokenStoreError as exc:
        print(f"{exc} Nothing was imported.")
        return 1
    finally:
        token_store.close()

    if version is None:
        print(
            "Google credentials are already stored in the database; kept them. "
            "Re-run with --replace to overwrite them with this file."
        )
        return 0
    print(f"Imported {path} into the database (version {version}). The file was not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
