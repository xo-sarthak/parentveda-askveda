"""
Central configuration — the ONE place that reads settings/secrets.

Why centralize? So no other file ever hardcodes a key or pokes at os.environ
directly. Every file imports `settings` from here. If a value changes, it changes
in one spot.

We use `pydantic-settings`: it reads environment variables (from a local `.env`
file in dev, or real env vars in production), validates their types, and hands us
a typed `settings` object. If a *required* setting is missing, it fails loudly at
startup instead of blowing up mid-request.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Load from a local `.env` file. In production the host sets real env vars and
    # there is no `.env` — pydantic just reads the env vars instead. `extra="ignore"`
    # means unknown vars in the file don't crash us.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App basics (Phase 0) ---
    app_name: str = "AskVeda"
    environment: str = "local"  # "local" while developing, "production" once deployed

    # --- Supabase (Phase 2) — the shared database ---
    # No defaults → REQUIRED. If missing from .env, the service fails loudly at
    # startup (much better than a confusing crash mid-request). service_role is
    # the powerful server-side key that bypasses row security.
    supabase_url: str
    supabase_service_role_key: str

    # --- Embeddings (Phase 2) ---
    embed_model: str = "BAAI/bge-small-en-v1.5"  # 384-dim — MUST match the vector(384) column

    # --- The LLM (Phase 3) — Groq via the OpenAI-compatible API ---
    # llm_api_key is REQUIRED (your gsk_... key, in .env). base_url + model have
    # defaults so switching providers/models is a ONE-LINE change (or an env override):
    #   Groq → Together/OpenAI/etc. = change llm_base_url + llm_model, nothing else.
    llm_api_key: str
    llm_base_url: str = "https://api.groq.com/openai/v1"   # Groq's OpenAI-compatible endpoint
    # 2026-09-27: Groq retired llama-3.1-8b-instant, so every answer failed
    # with model_not_found. gpt-oss-20b is the user's choice: tested on our
    # prompts (format kept, grounded, cites the TTC reads), fast, cheap. It
    # "thinks" before answering, so `llm_reasoning_effort` keeps that low.
    # Was: llm_model: str = "llama-3.1-8b-instant"
    llm_model: str = "openai/gpt-oss-20b"
    # low | medium | high. Grounded summarising, not puzzle-solving: low keeps
    # the hidden reasoning (billed as output tokens) and the wait small. Sent
    # only to models that take it (see app/llm.py).
    llm_reasoning_effort: str = "low"
    llm_temperature: float = 0.2  # low = factual/consistent; we want grounded, not creative

    # --- Retrieval (Phase 3) ---
    retrieval_top_k: int = 3  # how many nearest content chunks to feed the LLM
    min_retrieval_similarity: float = 0.30  # below this = off-topic/gap → skip the LLM

    # --- 7-section structured response (feed rework) ---
    sections_retrieval_k: int = 25       # wider search to populate the pointer sections
    answer_context_k: int = 6            # how many top chunks feed the LLM's answer
    section_min_similarity: float = 0.55  # a pointer must be THIS relevant to appear
    section_max_items: int = 4           # max items shown per section

    # --- Cache + guardrails (Phase 4) ---
    cache_similarity_threshold: float = 0.95  # reuse a cached answer only if THIS close
    rate_limit_per_day: int = 20              # max questions per user per day
    daily_spend_cap_usd: float = 2.0          # global circuit breaker for today's spend
    # Reference token prices (Groq openai/gpt-oss-20b, 2026-09-27; confirm on
    # https://groq.com/pricing) — used to LOG cost + enforce the cap. Free tier =
    # $0 actually charged; these make cost measurable for later. With the old
    # model's prices the cap would have measured about half the real spend.
    # Were (llama-3.1-8b-instant): 0.05 / 0.08.
    llm_price_input_per_1m_usd: float = 0.075
    llm_price_output_per_1m_usd: float = 0.30

    # --- App door / auth (Phase 5) ---
    # In production the app sends its Supabase login token; we verify it with this
    # secret and read the user id. In local dev it can be unset — a dev fallback
    # user_key is used instead (see app/auth.py). require_auth flips that off.
    supabase_jwt_secret: str | None = None
    require_auth: bool = False  # local dev = False (dev fallback ok); production = True

    # --- Admin: reindex + metrics (Phase 8) ---
    # Guards POST /reindex and GET /metrics (both public URLs that do real work or
    # expose numbers). Configure the same value in Directus's webhook.
    reindex_secret: str | None = None

    # --- Trusted-web fallback + flywheel (Phase 7) ---
    # When OUR content can't answer, we may answer from a WHITELIST of
    # authoritative medical sources rather than dead-ending. Never the open web.
    web_fallback_enabled: bool = True
    # Free tier at tavily.com. Without a key the fallback simply stays off and we
    # return the honest decline — nothing breaks.
    tavily_api_key: str | None = None
    web_trusted_domains: str = (
        "nhs.uk,acog.org,who.int,mayoclinic.org,aap.org,healthychildren.org,"
        "nhp.gov.in,main.icmr.nic.in,fogsi.org"
    )
    web_max_results: int = 3
    # Auto-draft answered gaps for an editor to review. NEVER auto-publishes.
    flywheel_enabled: bool = True

    # --- WhatsApp / MSG91 (Phase 6) ---
    # MSG91 is the BSP (Business Solution Provider) that sits between us and
    # WhatsApp. All three are unset until the number is live — the sender stays in
    # mock mode, so the whole path is testable today without an account.
    msg91_auth_key: str | None = None
    msg91_whatsapp_number: str | None = None
    # A shared secret we require on the webhook, so strangers can't POST fake
    # messages at us. Configure the same value in MSG91's webhook settings.
    msg91_webhook_secret: str | None = None
    # True = log the reply instead of calling a provider. Flip to False when live.
    whatsapp_mock_send: bool = True

    # --- Meta WhatsApp Cloud API (the free TEST number) ---
    # Lets us test a real WhatsApp round-trip before a company, a real number or
    # business verification exist. MSG91 resells this same Cloud API, so the
    # payloads are close to what production will send.
    #   meta_verify_token   — any string you invent; Meta echoes it back once when
    #                         you register the webhook (GET handshake).
    #   meta_access_token   — temporary token from the app dashboard (~24h) or a
    #                         permanent System User token.
    #   meta_phone_number_id— the TEST number's id (not the number itself).
    meta_verify_token: str | None = None
    meta_access_token: str | None = None
    meta_phone_number_id: str | None = None


# One shared instance, imported everywhere as: `from app.config import settings`
settings = Settings()
