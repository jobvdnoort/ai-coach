"""LLM client for AI Coach (OpenAI-compatible chat completions API)."""

from __future__ import annotations

from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BASE_URL,
    CONF_COACH_STYLE,
    CONF_MODEL,
    DEFAULT_BASE_URL,
    DEFAULT_COACH_STYLE,
    DEFAULT_MODEL,
    STYLE_PROMPTS,
)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)


class CoachError(Exception):
    """Raised when the LLM backend fails."""


class CoachClient:
    """Builds the prompt and calls the configured LLM."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    def _system_prompt(
        self,
        user_name: str,
        weights: list[dict[str, Any]],
        trainings: list[dict[str, Any]],
    ) -> str:
        style = self._entry.options.get(CONF_COACH_STYLE, DEFAULT_COACH_STYLE)
        parts = [
            STYLE_PROMPTS.get(style, STYLE_PROMPTS[DEFAULT_COACH_STYLE]),
            f"You are coaching {user_name}. Keep answers concise and actionable.",
        ]
        if weights:
            lines = "\n".join(f"- {w['measured_at']}: {w['weight_kg']} kg" for w in weights)
            parts.append(f"Recent weight measurements (newest first):\n{lines}")
        if trainings:
            lines = "\n".join(
                f"- {t['performed_at']}: {t['activity']}"
                + (f", {t['duration_min']} min" if t["duration_min"] else "")
                + (f", {t['distance_km']} km" if t["distance_km"] else "")
                for t in trainings
            )
            parts.append(f"Recent training sessions (newest first):\n{lines}")
        return "\n\n".join(parts)

    async def async_reply(
        self,
        *,
        user_name: str,
        history: list[dict[str, Any]],
        weights: list[dict[str, Any]],
        trainings: list[dict[str, Any]],
    ) -> str:
        """Return the coach's reply to the conversation in `history`."""
        base_url = self._entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL).rstrip("/")
        payload = {
            "model": self._entry.options.get(CONF_MODEL, DEFAULT_MODEL),
            "messages": [
                {"role": "system", "content": self._system_prompt(user_name, weights, trainings)},
                *({"role": m["role"], "content": m["content"]} for m in history),
            ],
        }
        headers = {"Authorization": f"Bearer {self._entry.data[CONF_API_KEY]}"}
        session = async_get_clientsession(self._hass)

        try:
            async with session.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise CoachError(f"LLM API returned {resp.status}: {body[:200]}")
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CoachError(f"Error talking to LLM API: {err}") from err

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as err:
            raise CoachError("Unexpected response from LLM API") from err
