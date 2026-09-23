"""LLM client for AI Coach (OpenAI-compatible chat completions API)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BASE_URL,
    CONF_COACH_STYLE,
    CONF_MODEL,
    CONF_USE_CUSTOM_MODEL,
    DEFAULT_BASE_URL,
    DEFAULT_COACH_STYLE,
    DEFAULT_MODEL,
    STYLE_PROMPTS,
)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_OPENAI_PATH = "/v1beta/openai"

# Migrate retired IDs for old entries. A model explicitly entered through the
# custom-model flow is never rewritten.
GEMINI_MODEL_REPLACEMENTS = {
    "gemini-1.5-flash": "gemini-3.6-flash",
    "gemini-1.5-flash-latest": "gemini-3.6-flash",
    "gemini-2.5-flash": "gemini-3.6-flash",
}


class CoachError(Exception):
    """Raised when the LLM backend fails."""


class CoachClient:
    """Builds the prompt and calls the configured LLM."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    @staticmethod
    def _chat_completions_url(base_url: str) -> tuple[str, bool]:
        """Return an OpenAI Chat Completions URL and whether it is Gemini.

        Google documents the Gemini compatibility endpoint as:
        https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
        """
        parsed = urlsplit(base_url.strip())
        is_gemini = parsed.hostname == GEMINI_HOST

        if is_gemini:
            # Do not let a missing/wrong trailing path accidentally target the
            # native generateContent API. Always use Gemini's OpenAI facade.
            path = GEMINI_OPENAI_PATH
        else:
            path = parsed.path.rstrip("/")

        if path.endswith("/chat/completions"):
            endpoint_path = path
        else:
            endpoint_path = f"{path}/chat/completions"

        return (
            urlunsplit(
                (parsed.scheme, parsed.netloc, endpoint_path, parsed.query, "")
            ),
            is_gemini,
        )

    @staticmethod
    def _model_name(
        configured_model: str, is_gemini: bool, use_custom_model: bool
    ) -> str:
        """Return the model ID expected by the selected OpenAI endpoint."""
        model = configured_model.strip()
        if not is_gemini:
            return model

        # Native Gemini APIs sometimes expose IDs as "models/<id>", while the
        # OpenAI-compatible API requires the bare model ID.
        if model.startswith("models/"):
            model = model.removeprefix("models/")
        if use_custom_model:
            return model
        return GEMINI_MODEL_REPLACEMENTS.get(model, model)

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
        endpoint, is_gemini = self._chat_completions_url(base_url)
        model = self._model_name(
            self._entry.options.get(CONF_MODEL, DEFAULT_MODEL),
            is_gemini,
            self._entry.options.get(CONF_USE_CUSTOM_MODEL, False),
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_prompt(user_name, weights, trainings)},
                *({"role": m["role"], "content": m["content"]} for m in history),
            ],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._entry.data[CONF_API_KEY]}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        session = async_get_clientsession(self._hass)

        try:
            async with session.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    detail = body[:500]
                    try:
                        error_data = json.loads(body)
                        if isinstance(error_data, list) and error_data:
                            error_data = error_data[0]
                        if isinstance(error_data, dict):
                            detail = error_data.get("error", {}).get(
                                "message", detail
                            )
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    raise CoachError(
                        f"LLM API returned {resp.status} for model {model}: {detail}"
                    )
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CoachError(f"Error talking to LLM API: {err}") from err

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as err:
            raise CoachError("Unexpected response from LLM API") from err
