"""The AI Coach integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .coach import CoachClient
from .const import CONF_TELEGRAM_BOT_TOKEN, DB_FILENAME, DOMAIN
from .database import CoachDatabase
from .lovelace import async_register_card
from .telegram import TelegramBot

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class AICoachData:
    """Runtime objects shared by the WebSocket API and Telegram bot."""

    db: CoachDatabase
    coach: CoachClient
    telegram: TelegramBot | None


type AICoachConfigEntry = ConfigEntry[AICoachData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register process-wide resources exactly once."""
    websocket_api.async_register(hass)
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AICoachConfigEntry) -> bool:
    db = CoachDatabase(hass, hass.config.path(DB_FILENAME))
    await db.async_open()
    coach = CoachClient(hass, entry, db)
    telegram: TelegramBot | None = None
    token = entry.data.get(CONF_TELEGRAM_BOT_TOKEN, "").strip()
    if token:
        telegram = TelegramBot(hass, token, db, coach)
        await telegram.async_start()
    entry.runtime_data = AICoachData(db=db, coach=coach, telegram=telegram)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AICoachConfigEntry) -> bool:
    if entry.runtime_data.telegram is not None:
        await entry.runtime_data.telegram.async_stop()
    await entry.runtime_data.db.async_close()
    return True
