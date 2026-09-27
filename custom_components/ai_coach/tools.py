"""Function-calling tools that let the coach store structured data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
import json
import re
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import LOG_CATEGORIES, MEAL_TYPES, PLAN_TYPES

if TYPE_CHECKING:
    from .database import CoachDatabase

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WHEN_DESCRIPTION = (
    "Local date/time it happened, ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM). "
    "Omit if it happened just now."
)
GET_LOGS_LIMIT = 50


def _function(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


SAVE_PROFILE_TOOL = _function(
    "save_user_profile",
    "Save the new user's completed profile. Call only after the user has "
    "provided their preferred name, current weight, and fitness goals.",
    {
        "name": {"type": "string", "description": "The user's preferred name."},
        "current_weight": {
            "type": "number",
            "description": "The user's current weight in kilograms.",
        },
        "goals": {"type": "string", "description": "The user's fitness goals."},
    },
    ["name", "current_weight", "goals"],
)

UPDATE_PROFILE_TOOL = _function(
    "update_profile",
    "Update the user's preferred name or fitness goals when they change.",
    {
        "name": {"type": "string", "description": "New preferred name."},
        "goals": {"type": "string", "description": "Updated fitness goals."},
    },
    [],
)

DATA_TOOLS = [
    _function(
        "log_meal",
        "Log food or drinks the user ate/drank. Call once per meal or snack. "
        "Estimate calories and macros when the user does not give them.",
        {
            "description": {
                "type": "string",
                "description": "What was eaten/drunk, including portions.",
            },
            "meal_type": {"type": "string", "enum": MEAL_TYPES},
            "calories": {"type": "number", "description": "Estimated kcal."},
            "protein_g": {"type": "number"},
            "carbs_g": {"type": "number"},
            "fat_g": {"type": "number"},
            "notes": {"type": "string"},
            "eaten_at": {"type": "string", "description": WHEN_DESCRIPTION},
        },
        ["description"],
    ),
    _function(
        "log_training",
        "Log a training session or physical activity (run, gym, walk, bike...).",
        {
            "activity": {"type": "string", "description": "Type of activity."},
            "duration_min": {"type": "number"},
            "distance_km": {"type": "number"},
            "intensity": {
                "type": "string",
                "description": "e.g. easy, moderate, hard, intervals.",
            },
            "notes": {"type": "string"},
            "performed_at": {"type": "string", "description": WHEN_DESCRIPTION},
        },
        ["activity"],
    ),
    _function(
        "log_weight",
        "Log a body weight measurement.",
        {
            "weight_kg": {"type": "number"},
            "note": {"type": "string"},
            "measured_at": {"type": "string", "description": WHEN_DESCRIPTION},
        },
        ["weight_kg"],
    ),
    _function(
        "log_wellbeing",
        "Log how the user feels: mood, energy, sleep, stress, soreness, illness.",
        {
            "feeling": {
                "type": "string",
                "description": "Short summary of how they feel.",
            },
            "energy_level": {
                "type": "integer",
                "description": "Energy from 1 (exhausted) to 10 (great).",
            },
            "sleep_hours": {"type": "number"},
            "notes": {"type": "string"},
            "recorded_at": {"type": "string", "description": WHEN_DESCRIPTION},
        },
        ["feeling"],
    ),
    _function(
        "save_plan",
        "Save a meal plan or training/running plan that you agreed on with the "
        "user. Replaces the current active plan of the same type, so always "
        "include the complete plan, not only the changes.",
        {
            "plan_type": {"type": "string", "enum": PLAN_TYPES},
            "title": {"type": "string"},
            "content": {
                "type": "string",
                "description": "The complete plan in Markdown.",
            },
        },
        ["plan_type", "title", "content"],
    ),
    _function(
        "remember",
        "Remember something important for future conversations. Use an expiry "
        "for temporary situations or upcoming events (e.g. 'BBQ tonight', "
        "'holiday next week', 'sore knee'). Omit the expiry for lasting facts "
        "(allergies, injuries, preferences, schedule).",
        {
            "note": {"type": "string", "description": "Short, self-contained note."},
            "expires_at": {
                "type": "string",
                "description": (
                    "Local date/time after which the note is irrelevant, ISO "
                    "format (YYYY-MM-DD or YYYY-MM-DDTHH:MM)."
                ),
            },
        },
        ["note"],
    ),
    _function(
        "forget",
        "Delete a remembered note that is no longer true or relevant.",
        {"note_id": {"type": "integer"}},
        ["note_id"],
    ),
    _function(
        "get_logs",
        "Look up stored data older than what is shown in the system prompt.",
        {
            "category": {"type": "string", "enum": LOG_CATEGORIES},
            "days": {
                "type": "integer",
                "description": "How many days back to look (1-365).",
            },
        },
        ["category"],
    ),
]


def tool_definitions(is_onboarded: bool) -> list[dict[str, Any]]:
    if not is_onboarded:
        return [SAVE_PROFILE_TOOL, *DATA_TOOLS]
    return [UPDATE_PROFILE_TOOL, *DATA_TOOLS]


class ToolArgumentError(Exception):
    """Raised for invalid tool arguments; reported back to the LLM."""


def _text(args: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ToolArgumentError(f"'{key}' is required")
        return None
    return str(value).strip()


def _number(
    args: dict[str, Any],
    key: str,
    *,
    required: bool = False,
    minimum: float = 0,
    maximum: float = 100_000,
) -> float | None:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolArgumentError(f"'{key}' is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ToolArgumentError(f"'{key}' must be a number") from err
    if not minimum <= number <= maximum:
        raise ToolArgumentError(f"'{key}' must be between {minimum} and {maximum}")
    return number


def _when(
    args: dict[str, Any], key: str, *, end_of_day: bool = False
) -> datetime | None:
    """Parse a local date/time the LLM supplied; naive values are local."""
    text = _text(args, key)
    if text is None:
        return None
    if DATE_ONLY.fullmatch(text):
        day = dt_util.parse_date(text)
        parsed = (
            datetime.combine(day, time(23, 59, 59) if end_of_day else time(12, 0))
            if day
            else None
        )
    else:
        parsed = dt_util.parse_datetime(text)
    if parsed is None:
        raise ToolArgumentError(f"'{key}' is not a valid ISO date/time: {text}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def local_time(iso: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a stored UTC timestamp in Home Assistant's local time zone."""
    parsed = dt_util.parse_datetime(iso)
    if parsed is None:
        return iso
    return dt_util.as_local(parsed).strftime(fmt)


def _past(value: datetime | None) -> datetime | None:
    """Logged events cannot happen in the future."""
    if value is None or value > dt_util.now():
        return None
    return value


class CoachTools:
    """Executes tool calls for one Home Assistant user."""

    def __init__(self, db: CoachDatabase, user_id: str) -> None:
        self._db = db
        self._user_id = user_id
        self.summaries: list[str] = []
        self._handlers: dict[
            str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
        ] = {
            "save_user_profile": self._save_user_profile,
            "update_profile": self._update_profile,
            "log_meal": self._log_meal,
            "log_training": self._log_training,
            "log_weight": self._log_weight,
            "log_wellbeing": self._log_wellbeing,
            "save_plan": self._save_plan,
            "remember": self._remember,
            "forget": self._forget,
            "get_logs": self._get_logs,
        }

    async def async_call(self, name: str, raw_arguments: Any) -> dict[str, Any]:
        """Run a tool; errors are returned to the LLM instead of raised."""
        handler = self._handlers.get(name)
        if handler is None:
            return {"success": False, "error": f"Unknown tool '{name}'"}
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            ) or {}
            if not isinstance(arguments, dict):
                raise ToolArgumentError("Arguments must be a JSON object")
            return await handler(arguments)
        except json.JSONDecodeError:
            return {"success": False, "error": "Arguments were not valid JSON"}
        except ToolArgumentError as err:
            return {"success": False, "error": str(err)}

    def _saved(self, summary: str, **extra: Any) -> dict[str, Any]:
        self.summaries.append(summary)
        return {"success": True, "saved": summary, **extra}

    async def _save_user_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        name = _text(args, "name", required=True)
        goals = _text(args, "goals", required=True)
        weight = _number(args, "current_weight", required=True, minimum=20, maximum=400)
        await self._db.async_save_user_profile(self._user_id, name, weight, goals)
        return self._saved("profile", message="Profile saved; onboarding is complete.")

    async def _update_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        name = _text(args, "name")
        goals = _text(args, "goals")
        if name is None and goals is None:
            raise ToolArgumentError("Provide 'name' and/or 'goals'")
        await self._db.async_update_profile(self._user_id, name, goals)
        return self._saved("profile")

    async def _log_meal(self, args: dict[str, Any]) -> dict[str, Any]:
        meal_type = _text(args, "meal_type")
        if meal_type is not None and meal_type not in MEAL_TYPES:
            meal_type = None
        description = _text(args, "description", required=True)
        entry_id = await self._db.async_add_meal(
            self._user_id,
            description,
            meal_type=meal_type,
            calories=_number(args, "calories", maximum=20_000),
            protein_g=_number(args, "protein_g", maximum=2_000),
            carbs_g=_number(args, "carbs_g", maximum=2_000),
            fat_g=_number(args, "fat_g", maximum=2_000),
            notes=_text(args, "notes"),
            eaten_at=_past(_when(args, "eaten_at")),
        )
        return self._saved(f"meal ({description})", id=entry_id)

    async def _log_training(self, args: dict[str, Any]) -> dict[str, Any]:
        activity = _text(args, "activity", required=True)
        entry_id = await self._db.async_add_training(
            self._user_id,
            activity,
            duration_min=_number(args, "duration_min", maximum=2_000),
            distance_km=_number(args, "distance_km", maximum=1_000),
            intensity=_text(args, "intensity"),
            notes=_text(args, "notes"),
            performed_at=_past(_when(args, "performed_at")),
        )
        return self._saved(f"training ({activity})", id=entry_id)

    async def _log_weight(self, args: dict[str, Any]) -> dict[str, Any]:
        weight = _number(args, "weight_kg", required=True, minimum=20, maximum=400)
        entry_id = await self._db.async_add_weight(
            self._user_id,
            weight,
            note=_text(args, "note"),
            measured_at=_past(_when(args, "measured_at")),
        )
        return self._saved(f"weight ({weight:g} kg)", id=entry_id)

    async def _log_wellbeing(self, args: dict[str, Any]) -> dict[str, Any]:
        feeling = _text(args, "feeling", required=True)
        energy = _number(args, "energy_level", minimum=1, maximum=10)
        entry_id = await self._db.async_add_wellbeing(
            self._user_id,
            feeling,
            energy_level=round(energy) if energy is not None else None,
            sleep_hours=_number(args, "sleep_hours", maximum=24),
            notes=_text(args, "notes"),
            recorded_at=_past(_when(args, "recorded_at")),
        )
        return self._saved(f"wellbeing ({feeling})", id=entry_id)

    async def _save_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        plan_type = _text(args, "plan_type", required=True)
        if plan_type not in PLAN_TYPES:
            raise ToolArgumentError(f"'plan_type' must be one of {PLAN_TYPES}")
        title = _text(args, "title", required=True)
        content = _text(args, "content", required=True)
        plan_id = await self._db.async_save_plan(
            self._user_id, plan_type, title, content
        )
        return self._saved(f"{plan_type} plan ({title})", id=plan_id)

    async def _remember(self, args: dict[str, Any]) -> dict[str, Any]:
        note = _text(args, "note", required=True)
        expires_at = _when(args, "expires_at", end_of_day=True)
        if expires_at is not None and expires_at <= dt_util.now():
            raise ToolArgumentError("'expires_at' must be in the future")
        note_id = await self._db.async_add_note(self._user_id, note, expires_at)
        return self._saved(f"note ({note})", id=note_id)

    async def _forget(self, args: dict[str, Any]) -> dict[str, Any]:
        note_id = _number(args, "note_id", required=True, maximum=10**12)
        if not await self._db.async_delete_note(self._user_id, int(note_id)):
            raise ToolArgumentError(f"No note with id {int(note_id)}")
        return {"success": True}

    async def _get_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        category = _text(args, "category", required=True)
        days = _number(args, "days", minimum=1, maximum=365) or 7
        since = dt_util.utcnow() - timedelta(days=days)
        getters = {
            "meals": self._db.async_get_meals,
            "trainings": self._db.async_get_trainings,
            "weights": self._db.async_get_weights,
            "wellbeing": self._db.async_get_wellbeing,
            "plans": self._db.async_get_plans,
        }
        getter = getters.get(category or "")
        if getter is None:
            raise ToolArgumentError(f"'category' must be one of {LOG_CATEGORIES}")
        rows = await getter(self._user_id, GET_LOGS_LIMIT, since)
        entries = [
            {
                key: local_time(value) if key.endswith("_at") else value
                for key, value in row.items()
                if value is not None
            }
            for row in rows
        ]
        return {
            "success": True,
            "category": category,
            "days": int(days),
            "entries": entries,
            "truncated": len(entries) == GET_LOGS_LIMIT,
        }
