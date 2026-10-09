"""Test package.

Blank out the credential-store settings before ``app.config`` is imported, so
a DATABASE_URL in a developer's .env (possibly production Neon) is never used
by tests. Tests that need a database use tests/postgres.py.
"""

import os

os.environ["DATABASE_URL"] = ""
os.environ["GOOGLE_TOKEN_ENCRYPTION_KEY"] = ""
