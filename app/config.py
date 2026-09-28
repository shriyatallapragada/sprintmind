"""Runtime configuration, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Hindsight -------------------------------------------------------
    # "hindsight" talks to Hindsight Cloud / a self-hosted server.
    # "local" uses an in-process keyword store (offline UI dev + tests only).
    memory_backend: str = "hindsight"
    hindsight_base_url: str = "https://api.hindsight.vectorize.io"
    hindsight_api_key: str | None = None
    hindsight_timeout: float = 120.0
    bank_id: str = "sprintmind_platform_team"
    # Retain synchronously so a recall right after ingestion (live demo) sees the new facts.
    retain_async: bool = False
    # Store every Q&A back into memory so the agent learns from its interactions.
    learn_from_interactions: bool = True

    # --- Groq (OpenAI-compatible) ------------------------------------------
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_model: str = "openai/gpt-oss-20b"
    llm_timeout: float = 60.0
    # Groq's free tier counts prompt + max reply against 8k tokens/minute; without a cap every call
    # reserves ~8k and is rejected with 413. Reasoning tokens count toward this cap too.
    llm_max_tokens: int = 3500

    # --- Meeting recording / transcription ------------------------------------
    # "groq"  = Whisper on Groq (free tier, reuses GROQ_API_KEY, audio up to 25 MB ≈ 1h40 at 32 kbps)
    # "local" = faster-whisper on this machine (fully offline; `pip install faster-whisper`)
    # "none"  = disable audio; pasted transcripts still work
    transcriber: str = "groq"
    groq_transcribe_model: str = "whisper-large-v3-turbo"
    local_whisper_model: str = "base"
    max_audio_mb: float = 25.0
    meetings_dir: str = "data/meetings"
    keep_audio: bool = False

    # --- Delivery ledger / GitHub ---------------------------------------------
    ledger_path: str = "data/sprintmind.db"
    ticket_prefixes: str = "NW"                     # project keys that link PRs / branches to tickets
    ticket_url_template: str | None = None          # e.g. https://acme.atlassian.net/browse/{ticket}
    github_repo: str = "northwind/platform"         # used for demo events and links
    github_webhook_secret: str | None = None        # required to accept real webhooks
    team_timezone: str = "Asia/Kolkata"             # due dates and business days are evaluated here

    # --- Access control ---------------------------------------------------------
    # "demo":  the caller is whoever the X-SprintMind-User header names (the UI's "Viewing as").
    #          Convenient for demos, NOT secure.
    # "token": callers must send `Authorization: Bearer <token>`; tokens map to people in API_TOKENS.
    auth_mode: str = "demo"
    api_tokens: str | None = None                   # "priya=<token>,neha=<token>"

    # --- Team / sprint context ---------------------------------------------
    team_name: str = "Northwind Platform Team"
    current_sprint: str = "14"
    team_file: str = "data/team.json"
    registry_file: str = "data/.registry.json"

    # --- API -----------------------------------------------------------------
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
