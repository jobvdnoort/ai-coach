"""LLM client for AI Coach (OpenAI-compatible chat completions API)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_URL,
    CONF_COACH_STYLE,
    CONF_MODEL,
    CONF_USE_CUSTOM_MODEL,
    DEFAULT_BASE_URL,
    DEFAULT_COACH_STYLE,
    DEFAULT_MODEL,
    HISTORY_CONTEXT_LIMIT,
    HISTORY_CONTEXT_MAX_AGE_HOURS,
    HISTORY_CONTEXT_MAX_CHARS,
    STYLE_PROMPTS,
)
from .tools import CoachTools, local_time, tool_definitions

if TYPE_CHECKING:
    from .database import CoachDatabase

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=90)
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 10
MAX_TOOL_ROUNDS = 5
PLAN_PROMPT_MAX_CHARS = 4000

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

MEMORY_PROMPT = """\
Memory rules (important):
- You only see the last few chat messages. Anything worth remembering must be \
stored with a tool, otherwise it is forgotten.
- Food or drinks the user had -> log_meal (estimate kcal and macros). Training \
or activity -> log_training. Body weight -> log_weight. How they feel (mood, \
energy, sleep, stress, soreness, illness) -> log_wellbeing.
- A meal plan or training/running plan you agree on together -> save_plan with \
the complete plan.
- Upcoming events or temporary situations that affect coaching (e.g. "BBQ \
tonight", "party on Saturday", "holiday next week") -> remember with an \
expiry, then use it proactively to help the user make good food, drink and \
training choices. Lasting facts (allergies, injuries, preferences, schedule) \
-> remember without expiry. Use forget when a note is no longer true.
- Store things silently while you reply normally; do not ask permission for \
obvious facts. Check the data below first so you never log something twice.
- Use get_logs when you need data older than what is shown below."""


class CoachError(Exception):
    """Raised when the LLM backend fails."""

    user_message = (
        "Sorry, I could not generate a response right now. Please try again "
        "in a moment."
    )


class CoachRateLimitError(CoachError):
    user_message = (
        "The AI service is rate limiting me or the API quota is used up. "
        "Please try again in a minute."
    )


class CoachTimeoutError(CoachError):
    user_message = "The AI service took too long to answer. Please try again."


class CoachAuthError(CoachError):
    user_message = (
        "The AI service rejected the API key or model. Please check the AI "
        "Coach configuration in Home Assistant."
    )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content.strip() if isinstance(content, str) else ""


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

    async def _async_system_prompt(
        self, user_id: str, user_name: str, profile: dict[str, Any]
    ) -> str:
        style = self._entry.options.get(CONF_COACH_STYLE, DEFAULT_COACH_STYLE)
        display_name = profile.get("name") or user_name
        now = dt_util.now()
        start_of_today = dt_util.start_of_local_day()

        notes = await self._db.async_get_active_notes(user_id)
        plans = await self._db.async_get_active_plans(user_id)
        meals = await self._db.async_get_meals(
            user_id, 30, start_of_today - timedelta(days=2)
        )
        trainings = await self._db.async_get_trainings(
            user_id, 10, now - timedelta(days=14)
        )
        weights = await self._db.async_get_weights(user_id, 5)
        wellbeing = await self._db.async_get_wellbeing(
            user_id, 5, now - timedelta(days=7)
        )

        parts = [
            STYLE_PROMPTS.get(style, STYLE_PROMPTS[DEFAULT_COACH_STYLE]),
            f"You are coaching {display_name}. Keep answers concise and "
            "actionable; they are often read on a phone.",
            f"Current local time: {now:%A %d %B %Y, %H:%M} "
            f"({dt_util.get_default_time_zone()}).",
            MEMORY_PROMPT,
        ]
        if not profile["is_onboarded"]:
            parts.append(ONBOARDING_PROMPT)
        elif profile.get("goals"):
            parts.append(f"The user's stated fitness goals: {profile['goals']}")

        if notes:
            lines = "\n".join(
                f"- #{n['id']}: {n['note']}"
                + (
                    f" (until {local_time(n['expires_at'], '%a %d %b %H:%M')})"
                    if n["expires_at"]
                    else ""
                )
                for n in notes
            )
            parts.append(f"Things you remembered about the user:\n{lines}")

        for plan in plans:
            content = plan["content"]
            if len(content) > PLAN_PROMPT_MAX_CHARS:
                content = content[:PLAN_PROMPT_MAX_CHARS] + "\n[…truncated]"
            parts.append(
                f"Active {plan['plan_type']} plan \"{plan['title']}\" "
                f"(saved {local_time(plan['created_at'], '%d %b %Y')}):\n{content}"
            )

        if meals:
            today_iso = dt_util.as_utc(start_of_today).isoformat()
            today = [m for m in meals if m["eaten_at"] >= today_iso]
            earlier = [m for m in meals if m["eaten_at"] < today_iso]
            if today:
                kcal = sum(m["calories"] or 0 for m in today)
                protein = sum(m["protein_g"] or 0 for m in today)
                totals = f"~{kcal:g} kcal" + (
                    f", ~{protein:g} g protein" if protein else ""
                )
                parts.append(
                    f"Meals logged today ({totals}):\n"
                    + self._meal_lines(reversed(today))
                )
            if earlier:
                parts.append(
                    "Meals logged the previous 2 days:\n"
                    + self._meal_lines(reversed(earlier))
                )

        if trainings:
            lines = "\n".join(
                f"- {local_time(t['performed_at'], '%a %d %b %H:%M')}: {t['activity']}"
                + (f", {t['duration_min']:g} min" if t["duration_min"] else "")
                + (f", {t['distance_km']:g} km" if t["distance_km"] else "")
                + (f", {t['intensity']}" if t["intensity"] else "")
                + (f" ({t['notes']})" if t["notes"] else "")
                for t in trainings
            )
            parts.append(f"Training last 14 days (newest first):\n{lines}")

        if weights:
            lines = "\n".join(
                f"- {local_time(w['measured_at'], '%d %b %Y')}: {w['weight_kg']:g} kg"
                for w in weights
            )
            parts.append(f"Recent weight measurements (newest first):\n{lines}")

        if wellbeing:
            lines = "\n".join(
                f"- {local_time(w['recorded_at'], '%a %d %b %H:%M')}: {w['feeling']}"
                + (f", energy {w['energy_level']}/10" if w["energy_level"] else "")
                + (f", slept {w['sleep_hours']:g} h" if w["sleep_hours"] else "")
                + (f" ({w['notes']})" if w["notes"] else "")
                for w in wellbeing
            )
            parts.append(f"How the user felt recently (newest first):\n{lines}")

        return "\n\n".join(parts)

    @staticmethod
    def _meal_lines(meals: Any) -> str:
        return "\n".join(
            f"- {local_time(m['eaten_at'], '%a %H:%M')}"
            + (f" {m['meal_type']}" if m["meal_type"] else "")
            + f": {m['description']}"
            + (f" (~{m['calories']:g} kcal)" if m["calories"] else "")
            for m in meals
        )

    async def _async_history(self, user_id: str) -> list[dict[str, Any]]:
        """Return a short, recent conversation window for the LLM."""
        since = dt_util.utcnow() - timedelta(hours=HISTORY_CONTEXT_MAX_AGE_HOURS)
        history = await self._db.async_get_history(
            user_id, HISTORY_CONTEXT_LIMIT, since
        )
        messages = []
        for message in history:
            content = message["content"]
            if len(content) > HISTORY_CONTEXT_MAX_CHARS:
                content = content[:HISTORY_CONTEXT_MAX_CHARS] + " […]"
            messages.append({"role": message["role"], "content": content})
        return messages

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

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with session.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    status = resp.status
                    retry_after = resp.headers.get("Retry-After")
                    body = await resp.text()
            except TimeoutError as err:
                if attempt < MAX_ATTEMPTS:
                    _LOGGER.debug("LLM request timed out, retrying")
                    continue
                raise CoachTimeoutError(
                    f"LLM API timed out for model {model}"
                ) from err
            except aiohttp.ClientError as err:
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(2 * attempt)
                    continue
                raise CoachError(f"Error talking to LLM API: {err}") from err

            if status in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                delay = self._retry_delay(retry_after, attempt)
                _LOGGER.debug(
                    "LLM API returned %s, retrying in %s s", status, delay
                )
                await asyncio.sleep(delay)
                continue
            if status >= 400:
                detail = self._error_detail(body)
                message = f"LLM API returned {status} for model {model}: {detail}"
                if status == 429:
                    raise CoachRateLimitError(message)
                if status in (401, 403, 404):
                    raise CoachAuthError(message)
                raise CoachError(message)
            break

        try:
            data = json.loads(body)
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as err:
            raise CoachError(
                f"Unexpected response from LLM API: {body[:300]}"
            ) from err
        finish_reason = data["choices"][0].get("finish_reason")
        if finish_reason not in (None, "stop", "tool_calls"):
            _LOGGER.debug("LLM finished with reason %s", finish_reason)
        return message

    @staticmethod
    def _retry_delay(retry_after: str | None, attempt: int) -> float:
        try:
            delay = float(retry_after) if retry_after else 2.0 * attempt
        except ValueError:
            delay = 2.0 * attempt
        return max(1.0, min(delay, MAX_RETRY_DELAY_SECONDS))

    @staticmethod
    def _error_detail(body: str) -> str:
        detail = body[:500]
        try:
            error_data = json.loads(body)
            if isinstance(error_data, list) and error_data:
                error_data = error_data[0]
            if isinstance(error_data, dict):
                detail = error_data.get("error", {}).get("message", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        return detail

    async def async_reply(self, *, user_id: str, user_name: str) -> str:
        """Return a reply for the user's latest message.

        The user's message must already be stored in the chat history. Any
        structured data the LLM extracts is stored through tool calls.
        """
        tools = CoachTools(self._db, user_id)
        conversation = await self._async_history(user_id)
        empty_retries = 1

        for _ in range(MAX_TOOL_ROUNDS):
            # Rebuilt every round so freshly logged data is visible and the
            # onboarding instructions disappear once the profile is saved.
            profile = await self._db.async_get_user(user_id)
            system_prompt = await self._async_system_prompt(
                user_id, user_name, profile
            )
            message = await self._async_completion(
                [{"role": "system", "content": system_prompt}, *conversation],
                tool_definitions(bool(profile["is_onboarded"])),
            )
            tool_calls = [
                call
                for call in message.get("tool_calls") or []
                if isinstance(call, dict)
            ]

            if not tool_calls:
                if content := _message_text(message):
                    return content
                if tools.summaries:
                    break
                if empty_retries:
                    empty_retries -= 1
                    continue
                raise CoachError("The LLM returned an empty response")

            for call in tool_calls:
                if not call.get("id"):
                    call["id"] = f"call_{secrets.token_hex(8)}"
            # Echo the calls back unchanged: Gemini needs its thought
            # signatures (extra_content) to continue a function-calling turn.
            conversation.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                function = call.get("function") or {}
                result = await tools.async_call(
                    function.get("name", ""), function.get("arguments")
                )
                _LOGGER.debug("Tool %s -> %s", function.get("name"), result)
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        if tools.summaries:
            return "Got it, I saved: " + ", ".join(tools.summaries) + "."
        raise CoachError("The LLM did not finish its response")
