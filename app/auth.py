"""
Auth (Phase 5) — turn an incoming request into a `user_key` (who is asking).

The user_key drives the per-user rate limit (and later, personalization).
- PRODUCTION: the app sends its Supabase login token as `Authorization: Bearer
  <jwt>`. We verify it with the project's JWT secret and read `sub` (the user id).
- LOCAL DEV: the app's Supabase auth may not be wired yet, so if there's no valid
  token we fall back to a dev key (or an `X-User-Key` header). This lets us test the
  whole path today. Set REQUIRE_AUTH=true (production) to reject unauthenticated
  calls instead of falling back.
"""

from fastapi import HTTPException

from app.config import settings


def resolve_user_key(authorization: str | None, x_user_key: str | None) -> str:
    """Return the caller's user_key, verifying a Supabase JWT when present."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    # Verify a real token if we have both a token and the secret to check it.
    if token and settings.supabase_jwt_secret:
        try:
            import jwt  # PyJWT

            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            sub = payload.get("sub")
            if sub:
                return f"app:{sub}"
        except Exception:
            if settings.require_auth:
                raise HTTPException(status_code=401, detail="Invalid or expired login token.")

    # No valid token.
    if settings.require_auth:
        raise HTTPException(status_code=401, detail="Login required.")

    # Dev fallback — identify by a provided key, else a shared dev key.
    return f"app:{x_user_key or 'dev-user'}"
