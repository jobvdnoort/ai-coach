"""Serve the AI Coach Lovelace card and load it on every dashboard."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CARD_FILENAME, CARD_URL, DOMAIN


async def async_register_card(hass: HomeAssistant) -> None:
    card_path = Path(__file__).parent / "frontend" / CARD_FILENAME
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
    )
    integration = await async_get_integration(hass, DOMAIN)
    # Version query string busts the browser cache after HACS updates.
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
