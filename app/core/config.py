from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv


load_dotenv()


DEFAULT_CAMPUS_MAP_VARIANT = "islanderhack"
# DEFAULT_CAMPUS_MAP_VARIANT = "primary"

DEFAULT_TEMPLATE_VARIANT = "smart_campus"
# DEFAULT_TEMPLATE_VARIANT = "classic"

@dataclass
class Settings:
    mongodb_uri: str = os.getenv("MONGODB_URI", "mongodb://mongo:27017/smartassist")
    secret_key: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production-12345")
    session_cookie: str = os.getenv("SESSION_COOKIE_NAME", "session")
    use_llm_followups: bool = os.getenv("USE_LLM_FOLLOWUPS", "1") == "1"
    followup_model: str = os.getenv("FOLLOWUP_MODEL", "gpt-4o-mini")
    debug_followups: bool = os.getenv("DEBUG_FOLLOWUPS", "1") == "1"
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    google_client_id: Optional[str] = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret: Optional[str] = os.getenv("GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    campus_map_variant: str = os.getenv("CAMPUS_MAP_VARIANT", DEFAULT_CAMPUS_MAP_VARIANT)
    template_variant: str = os.getenv("TEMPLATE_VARIANT", DEFAULT_TEMPLATE_VARIANT)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

print(
    "[BOOT] USE_LLM_FOLLOWUPS=", settings.use_llm_followups,
    "MODEL=", settings.followup_model,
    "OPENAI_KEY_PRESENT=", bool(settings.openai_api_key),
    "MAP_VARIANT=", settings.campus_map_variant,
    "TEMPLATE_VARIANT=", settings.template_variant,
)
