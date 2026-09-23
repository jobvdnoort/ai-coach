"""Constants for the AI Coach integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ai_coach"

DB_FILENAME: Final = "ai_coach.db"

CARD_FILENAME: Final = "ai-coach-card.js"
CARD_URL: Final = f"/{DOMAIN}/{CARD_FILENAME}"

CONF_BASE_URL: Final = "base_url"
CONF_MODEL: Final = "model"
CONF_TELEGRAM_BOT_TOKEN: Final = "telegram_bot_token"
CONF_COACH_STYLE: Final = "coach_style"

DEFAULT_BASE_URL: Final = "https://api.openai.com/v1"
DEFAULT_MODEL: Final = "gpt-4.1-mini"

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
