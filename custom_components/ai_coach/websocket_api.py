"""WebSocket commands used by the AI Coach Lovelace card.

The HA user is always taken from the authenticated connection
(`connection.user`, which is also what `connection.context(msg).user_id`
resolves to). The client never supplies a user id, so users cannot read or
write each other's history.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .coach import CoachError
from .const import DOMAIN, HISTORY_CONTEXT_LIMIT, MAX_MESSAGE_LENGTH, RECENT_DATA_LIMIT

if TYPE_CHECKING:
    from . import AICoachData

_LOGGER = logging.getLogger(__name__)


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_send_message)
    websocket_api.async_register_command(hass, ws_clear_history)
    websocket_api.async_register_command(hass, ws_generate_pairing_code)


def _get_data(hass: HomeAssistant) -> AICoachData | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


def _require_data(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> AICoachData | None:
    data = _get_data(hass)
    if data is None:
        connection.send_error(msg["id"], "not_loaded", "AI Coach is not set up")
    return data


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history",
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=500)),
    }
)
@websocket_api.async_response
async def ws_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the chat history of the connected user."""
    if (data := _require_data(hass, connection, msg)) is None:
        return
    messages = await data.db.async_get_history(connection.user.id, msg["limit"])
    connection.send_result(msg["id"], {"messages": messages})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/send_message",
        vol.Required("message"): vol.All(
            str, vol.Strip, vol.Length(min=1, max=MAX_MESSAGE_LENGTH)
        ),
    }
)
@websocket_api.async_response
async def ws_send_message(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Store the user's message, ask the coach, store and return the reply."""
    if (data := _require_data(hass, connection, msg)) is None:
        return

    user = connection.user
    user_message = await data.db.async_add_message(user.id, "user", msg["message"])
    history = await data.db.async_get_history(user.id, HISTORY_CONTEXT_LIMIT)
    weights = await data.db.async_get_weights(user.id, RECENT_DATA_LIMIT)
    trainings = await data.db.async_get_trainings(user.id, RECENT_DATA_LIMIT)

    try:
        reply = await data.coach.async_reply(
            user_id=user.id,
            user_name=user.name or "the user",
            history=history,
            weights=weights,
            trainings=trainings,
        )
    except CoachError as err:
        _LOGGER.warning("AI Coach reply failed: %s", err)
        connection.send_error(msg["id"], "llm_error", str(err))
        return

    assistant_message = await data.db.async_add_message(user.id, "assistant", reply)
    connection.send_result(
        msg["id"],
        {"user_message": user_message, "assistant_message": assistant_message},
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/clear_history"})
@websocket_api.async_response
async def ws_clear_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete the connected user's chat history."""
    if (data := _require_data(hass, connection, msg)) is None:
        return
    deleted = await data.db.async_clear_history(connection.user.id)
    connection.send_result(msg["id"], {"deleted": deleted})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/generate_pairing_code"}
)
@websocket_api.async_response
async def ws_generate_pairing_code(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a Telegram pairing code for the authenticated HA user."""
    if (data := _require_data(hass, connection, msg)) is None:
        return
    if data.telegram is None:
        connection.send_error(
            msg["id"],
            "telegram_not_configured",
            "Configure a Telegram bot token before linking",
        )
        return

    user_id = connection.context(msg).user_id
    if user_id is None:
        connection.send_error(
            msg["id"], "authentication_required", "No authenticated user"
        )
        return

    code = await data.db.async_generate_pairing_code(user_id)
    connection.send_result(msg["id"], {"pairing_code": code})
