"""LLM client for AI Coach (OpenAI-compatible chat completions API)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
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

if TYPE_CHECKING:
    from .database import CoachDatabase

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

ONBOARDING_PROMPT = (
    "This is a new user. Have a natural, conversational onboarding to find "
    "out their preferred name, current weight, and fitness goals. Do not ask "
    "everything at once. Call save_user_profile ONLY after all three values "
    "have been clearly provided by the user."
)

SAVE_PROFILE_TOOL = {
    "type": "function",
    "function": {
        "name": "save_user_profile",
        "description": (
            "Save the new user's completed profile. Call only after the user "
            "has provided their preferred name, current weight, and fitness goals."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The user's preferred name.",
                },
                "current_weight": {
                    "type": "number",
                    "description": "The user's current weight in kilograms.",
                },
                "goals": {
                    "type": "string",
                    "description": "The user's fitness goals.",
                },
            },
            "required": ["name", "current_weight", "goals"],
            "additionalProperties": False,
        },
    },
}


class CoachError(Exception):
    """Raised when the LLM backend fails."""


class CoachClient:
    """Builds the prompt and calls the configured LLM."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, db: CoachDatabase
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._db = db

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
        profile: dict[str, Any],
        weights: list[dict[str, Any]],
        trainings: list[dict[str, Any]],
    ) -> str:
        style = self._entry.options.get(CONF_COACH_STYLE, DEFAULT_COACH_STYLE)
        display_name = profile.get("name") or user_name
        parts = [
            STYLE_PROMPTS.get(style, STYLE_PROMPTS[DEFAULT_COACH_STYLE]),
            f"You are coaching {display_name}. Keep answers concise and actionable.",
        ]
        if not profile["is_onboarded"]:
            parts.append(ONBOARDING_PROMPT)
        elif profile.get("goals"):
            parts.append(f"The user's stated fitness goals: {profile['goals']}")
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

    async def _async_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call the configured OpenAI-compatible Chat Completions endpoint."""
        base_url = self._entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL).rstrip("/")
        endpoint, is_gemini = self._chat_completions_url(base_url)
        model = self._model_name(
            self._entry.options.get(CONF_MODEL, DEFAULT_MODEL),
            is_gemini,
            self._entry.options.get(CONF_USE_CUSTOM_MODEL, False),
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

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
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError
            return message
        except (KeyError, IndexError, TypeError) as err:
            raise CoachError("Unexpected response from LLM API") from err

    @staticmethod
    def _profile_arguments(tool_call: dict[str, Any]) -> tuple[str, float, str]:
        """Parse and validate save_user_profile function arguments."""
        try:
            arguments = tool_call["function"]["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            name = str(arguments["name"]).strip()
            current_weight = float(arguments["current_weight"])
            goals = str(arguments["goals"]).strip()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
            raise CoachError("The LLM returned invalid profile tool arguments") from err

        if not name or not goals or current_weight <= 0:
            raise CoachError("The LLM returned an incomplete onboarding profile")
        return name, current_weight, goals

    async def async_reply(
        self,
        *,
        user_id: str,
        user_name: str,
        history: list[dict[str, Any]],
        weights: list[dict[str, Any]],
        trainings: list[dict[str, Any]],
    ) -> str:
        """Return a reply, completing onboarding through a function call."""
        profile = await self._db.async_get_user(user_id)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._system_prompt(
                    user_name, profile, weights, trainings
                ),
            },
            *(
                {"role": message["role"], "content": message["content"]}
                for message in history
            ),
        ]

        # A second completion is needed after a successful tool call so the
        # user receives a natural acknowledgement instead of an empty reply.
        for _ in range(3):
            tools = None if profile["is_onboarded"] else [SAVE_PROFILE_TOOL]
            message = await self._async_completion(messages, tools)
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                raise CoachError("The LLM returned an empty response")

            tool_call = next(
                (
                    call
                    for call in tool_calls
                    if call.get("function", {}).get("name")
                    == "save_user_profile"
                ),
                None,
            )
            if tool_call is None or profile["is_onboarded"]:
                raise CoachError("The LLM requested an unsupported tool")
            tool_call_id = tool_call.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise CoachError("The LLM returned a tool call without an ID")

            name, current_weight, goals = self._profile_arguments(tool_call)
            await self._db.async_save_user_profile(
                user_id, name, current_weight, goals
            )
            profile = await self._db.async_get_user(user_id)

            messages[0] = {
                "role": "system",
                "content": self._system_prompt(
                    user_name, profile, weights, trainings
                ),
            }
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": [tool_call],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(
                        {
                            "success": True,
                            "message": "Profile saved; onboarding is complete.",
                        }
                    ),
                }
            )

        raise CoachError("The LLM did not finish its response")
