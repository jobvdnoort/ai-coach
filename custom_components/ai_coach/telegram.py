"""Asynchronous Telegram long-polling transport for AI Coach."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .coach import CoachClient, CoachError
from .const import HISTORY_CONTEXT_LIMIT, RECENT_DATA_LIMIT
from .database import CoachDatabase

_LOGGER = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 25
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=POLL_TIMEOUT_SECONDS + 10)
LINK_PATTERN = re.compile(r"^/link(?:@\w+)?\s+(\d{6})$", re.IGNORECASE)


class TelegramBot:
    """Receive Telegram updates without blocking Home Assistant's event loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        token: str,
        db: CoachDatabase,
        coach: CoachClient,
    ) -> None:
        self._hass = hass
        self._token = token
        self._db = db
        self._coach = coach
        self._offset = 0
        self._task: asyncio.Task[None] | None = None

    async def async_start(self) -> None:
        """Start the long-polling task."""
        if self._task is None:
            self._task = asyncio.create_task(
                self._poll_loop(), name="ai_coach_telegram_polling"
            )

    async def async_stop(self) -> None:
        """Cancel polling and wait for clean shutdown."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _telegram_request(
        self, method: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        session = async_get_clientsession(self._hass)
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        async with session.post(
            url, json=payload, timeout=HTTP_TIMEOUT
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400 or not data.get("ok"):
                description = data.get("description", await response.text())
                raise RuntimeError(
                    f"Telegram {method} failed ({response.status}): {description}"
                )
            return data

    async def _poll_loop(self) -> None:
        """Continuously poll Telegram; all waits and HTTP calls are async."""
        webhook_removed = False
        while True:
            try:
                if not webhook_removed:
                    # Telegram rejects getUpdates while a webhook is active.
                    await self._telegram_request(
                        "deleteWebhook", {"drop_pending_updates": False}
                    )
                    webhook_removed = True
                data = await self._telegram_request(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": POLL_TIMEOUT_SECONDS,
                        "allowed_updates": ["message"],
                    },
                )
                for update in data.get("result", []):
                    self._offset = max(self._offset, update["update_id"] + 1)
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Telegram polling failed; retrying")
                await asyncio.sleep(5)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not message or not isinstance(message.get("text"), str):
            return

        chat_id = int(message["chat"]["id"])
        text = message["text"].strip()
        link_match = LINK_PATTERN.fullmatch(text)
        if link_match:
            await self._handle_link(chat_id, link_match.group(1))
            return

        profile = await self._db.async_get_user_by_telegram(chat_id)
        if profile is None:
            await self._send_message(
                chat_id,
                "This chat is not linked yet. Open the AI Coach card in Home "
                "Assistant, select Link Telegram, then send /link followed by "
                "the six-digit code.",
            )
            return

        ha_user_id = profile["ha_user_id"]
        await self._db.async_add_message(
            ha_user_id, "user", text, source="telegram"
        )
        history = await self._db.async_get_history(
            ha_user_id, HISTORY_CONTEXT_LIMIT
        )
        weights = await self._db.async_get_weights(
            ha_user_id, RECENT_DATA_LIMIT
        )
        trainings = await self._db.async_get_trainings(
            ha_user_id, RECENT_DATA_LIMIT
        )

        try:
            reply = await self._coach.async_reply(
                user_id=ha_user_id,
                user_name=profile.get("name") or "the user",
                history=history,
                weights=weights,
                trainings=trainings,
            )
        except CoachError:
            _LOGGER.exception("AI Coach could not answer Telegram user")
            await self._send_message(
                chat_id, "Sorry, I could not generate a response right now."
            )
            return

        await self._db.async_add_message(
            ha_user_id, "assistant", reply, source="telegram"
        )
        await self._send_message(chat_id, reply)

    async def _handle_link(self, chat_id: int, pairing_code: str) -> None:
        profile = await self._db.async_link_telegram(pairing_code, chat_id)
        if profile is None:
            await self._send_message(
                chat_id,
                "That pairing code is invalid. Generate a new code from the "
                "AI Coach card and try again.",
            )
            return
        await self._send_message(
            chat_id,
            "Telegram is now linked to your Home Assistant AI Coach profile. "
            "You can start chatting here.",
        )

    async def _send_message(self, chat_id: int, text: str) -> None:
        # Telegram limits text messages to 4096 UTF-8 characters.
        await self._telegram_request(
            "sendMessage", {"chat_id": chat_id, "text": text[:4096]}
        )
