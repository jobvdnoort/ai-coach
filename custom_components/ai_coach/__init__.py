"""The AI Coach integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .coach import CoachClient
from .const import DB_FILENAME, DOMAIN
from .database import CoachDatabase
from .lovelace import async_register_card

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class AICoachData:
    """Runtime objects shared by the WebSocket API (and later the Telegram bot)."""

    db: CoachDatabase
    coach: CoachClient


type AICoachConfigEntry = ConfigEntry[AICoachData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register process-wide resources exactly once."""
    websocket_api.async_register(hass)
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AICoachConfigEntry) -> bool:
    db = CoachDatabase(hass, hass.config.path(DB_FILENAME))
    await db.async_open()
    entry.runtime_data = AICoachData(db=db, coach=CoachClient(hass, entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AICoachConfigEntry) -> bool:
    await entry.runtime_data.db.async_close()
    return True
