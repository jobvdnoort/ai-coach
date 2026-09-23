"""Constants for the AI Coach integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ai_coach"

DB_FILENAME: Final = "ai_coach.db"

CARD_FILENAME: Final = "ai-coach-card.js"
CARD_URL: Final = f"/{DOMAIN}/{CARD_FILENAME}"

CONF_BASE_URL: Final = "base_url"
CONF_PROVIDER: Final = "provider"
CONF_MODEL: Final = "model"
CONF_CUSTOM_MODEL: Final = "custom_model"
CONF_USE_CUSTOM_MODEL: Final = "use_custom_model"
CONF_TELEGRAM_BOT_TOKEN: Final = "telegram_bot_token"
CONF_COACH_STYLE: Final = "coach_style"

DEFAULT_BASE_URL: Final = "https://api.openai.com/v1"
DEFAULT_MODEL: Final = "gpt-4.1-mini"

PROVIDER_OPENAI: Final = "openai"
PROVIDER_GOOGLE_AI_STUDIO: Final = "google_ai_studio"
DEFAULT_PROVIDER: Final = PROVIDER_OPENAI

LLM_PROVIDERS: Final = [
    PROVIDER_GOOGLE_AI_STUDIO,
    PROVIDER_OPENAI,
]

PROVIDER_BASE_URLS: Final = {
    PROVIDER_GOOGLE_AI_STUDIO: (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    ),
    PROVIDER_OPENAI: "https://api.openai.com/v1",
}

PROVIDER_MODELS: Final = {
    PROVIDER_GOOGLE_AI_STUDIO: [
        "gemini-3.6-flash",
        "gemini-3.6-pro",
    ],
    PROVIDER_OPENAI: [
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4.1-nano",
        "gpt-4o-mini",
        "gpt-4o",
    ],
}

PROVIDER_DEFAULT_MODELS: Final = {
    PROVIDER_GOOGLE_AI_STUDIO: "gemini-3.6-flash",
    PROVIDER_OPENAI: DEFAULT_MODEL,
}

STYLE_GENTLE: Final = "gentle"
STYLE_BALANCED: Final = "balanced"
STYLE_STRICT: Final = "strict"
STYLE_DRILL_SERGEANT: Final = "drill_sergeant"

COACH_STYLES: Final = [STYLE_GENTLE, STYLE_BALANCED, STYLE_STRICT, STYLE_DRILL_SERGEANT]
DEFAULT_COACH_STYLE: Final = STYLE_BALANCED

STYLE_PROMPTS: Final = {
    STYLE_GENTLE: (
        "You are a warm, encouraging personal fitness coach. Celebrate small wins, "
        "never shame, and suggest gradual, sustainable changes."
    ),
    STYLE_BALANCED: (
        "You are a supportive but honest personal fitness coach. Give clear, "
        "practical advice and point out when the user is off track."
    ),
    STYLE_STRICT: (
        "You are a strict, no-nonsense personal fitness coach. Hold the user "
        "accountable, call out excuses directly, and set concrete targets."
    ),
    STYLE_DRILL_SERGEANT: (
        "You are a tough drill-sergeant style fitness coach. Be blunt, intense and "
        "demanding, but never insulting and never give unsafe advice."
    ),
}

HISTORY_CONTEXT_LIMIT: Final = 20
RECENT_DATA_LIMIT: Final = 5
MAX_MESSAGE_LENGTH: Final = 4000
