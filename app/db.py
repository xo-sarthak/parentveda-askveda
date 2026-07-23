"""
Supabase client — the one connection every file shares.

We build it with the SERVICE_ROLE key, which bypasses row-level security, so the
server has full read/write on our `veda_*` tables. (This key is server-side only —
it lives in `.env`, never in the app or a git commit.)
"""

from supabase import Client, create_client

from app.config import settings

# One shared client, imported as: `from app.db import supabase`
supabase: Client = create_client(
    settings.supabase_url,
    settings.supabase_service_role_key,
)
