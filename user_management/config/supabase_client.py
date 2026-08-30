"""
config/supabase_client.py
Shared Supabase client initialization.
Supports environment variables from .env or container environment.
"""

import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logger = logging.getLogger("user_management.supabase")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or ""
)

_client_instance: Client | None = None


def get_supabase_client() -> Client:
    """
    Returns the singleton Supabase client, initializing lazily if needed.
    """
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    url = os.getenv("SUPABASE_URL", SUPABASE_URL)
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or SUPABASE_KEY
    )

    if not url or not key:
        logger.error(
            "Supabase credentials missing: please set SUPABASE_URL and SUPABASE_KEY in environment."
        )
        raise ValueError("Supabase environment variables (SUPABASE_URL, SUPABASE_KEY) are missing.")

    _client_instance = create_client(url, key)
    logger.info(f"Supabase client initialized successfully for {url}")
    return _client_instance


class _LazySupabaseProxy:
    """
    Proxy object that delegates all calls to get_supabase_client()
    so existing code importing `supabase` directly continues to work.
    """

    def __getattr__(self, name):
        client = get_supabase_client()
        return getattr(client, name)


# Exported singleton / proxy client
supabase: Client = _LazySupabaseProxy()  # type: ignore
