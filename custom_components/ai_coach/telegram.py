"""Asynchronous Telegram long-polling transport for AI Coach."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .coach import CoachClient, CoachError
from .const import CHAT_RETENTION_LIMIT, MAX_MESSAGE_LENGTH
from .database import CoachDatabase

_LOGGER = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 25
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=POLL_TIMEOUT_SECONDS + 10)
LINK_PATTERN = re.compile(r"^/link(?:@\w+)?\s+(\d{6})$", re.IGNORECASE)
START_PATTERN = re.compile(r"^/(?:start|help)(?:@\w+)?\b", re.IGNORECASE)
TYPING_INTERVAL_SECONDS = 4

# Telegram's hard limit is 4096 characters after entity parsing; stay well
# below it so the HTML markup and multi-byte characters always fit.
MAX_CHUNK_LENGTH = 3500

NOT_LINKED_TEXT = (
    "This chat is not linked yet. Open the AI Coach card in Home Assistant, "
    "select Link Telegram, then send /link followed by the six-digit code."
)

CODE_BLOCK = re.compile(r"```[\w+-]*\n?(.*?)```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
ITALIC = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")
LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s\"]+)\)")
HEADER = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)
BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)


def _inline_to_html(text: str) -> str:
    """Convert the Markdown subset LLMs typically emit to Telegram HTML."""
    pieces = INLINE_CODE.split(text)
    out = []
    for index, piece in enumerate(pieces):
        if index % 2:
            out.append(f"<code>{html.escape(piece, quote=False)}</code>")
            continue
        piece = RULE.sub("", piece)
        piece = BULLET.sub(r"\1• ", piece)
        piece = html.escape(piece, quote=False)
        piece = HEADER.sub(r"<b>\1</b>", piece)
        piece = BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", piece)
        piece = ITALIC.sub(r"<i>\1</i>", piece)
        piece = LINK.sub(r'<a href="\2">\1</a>', piece)
        out.append(piece)
    return "".join(out)


def markdown_to_telegram_html(text: str) -> str:
    pieces = CODE_BLOCK.split(text)
    return "".join(
        f"<pre>{html.escape(piece.strip(chr(10)), quote=False)}</pre>"
        if index % 2
        else _inline_to_html(piece)
        for index, piece in enumerate(pieces)
    )


def split_message(text: str, limit: int = MAX_CHUNK_LENGTH) -> list[str]:
    """Split text into chunks, preferring paragraph, then line boundaries."""
    chunks: list[str] = []
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


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
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Telegram polling failed; retrying")
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                self._offset = max(self._offset, update["update_id"] + 1)
                try:
                    await self._handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception("Could not handle Telegram update")

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not message or not isinstance(message.get("text"), str):
            return

        chat_id = int(message["chat"]["id"])
        text = message["text"].strip()[:MAX_MESSAGE_LENGTH]
        if not text:
            return
        link_match = LINK_PATTERN.fullmatch(text)
        if link_match:
            await self._handle_link(chat_id, link_match.group(1))
            return

        profile = await self._db.async_get_user_by_telegram(chat_id)
        if profile is None:
            await self._send_message(chat_id, NOT_LINKED_TEXT)
            return
        if START_PATTERN.match(text):
            await self._send_message(
                chat_id,
                "Your coach is ready. Tell me what you ate, how your training "
                "went, your weight, or how you feel.",
            )
            return

        ha_user_id = profile["ha_user_id"]
        await self._db.async_add_message(
            ha_user_id,
            "user",
            text,
            source="telegram",
            retention=CHAT_RETENTION_LIMIT,
        )

        typing = asyncio.create_task(self._keep_typing(chat_id))
        try:
            reply = await self._coach.async_reply(
                user_id=ha_user_id,
                user_name=profile.get("name") or "the user",
            )
        except CoachError as err:
            _LOGGER.warning("AI Coach could not answer Telegram user: %s", err)
            await self._send_message(chat_id, err.user_message)
            return
        finally:
            typing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing

        await self._db.async_add_message(
            ha_user_id,
            "assistant",
            reply,
            source="telegram",
            retention=CHAT_RETENTION_LIMIT,
        )
        await self._send_message(chat_id, reply, markdown=True)

    async def _keep_typing(self, chat_id: int) -> None:
        """Show 'typing…' until cancelled; Telegram clears it after ~5 s."""
        while True:
            try:
                await self._telegram_request(
                    "sendChatAction", {"chat_id": chat_id, "action": "typing"}
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - cosmetic only
                _LOGGER.debug("Could not send typing action: %s", err)
            await asyncio.sleep(TYPING_INTERVAL_SECONDS)

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

    async def _send_message(
        self, chat_id: int, text: str, *, markdown: bool = False
    ) -> None:
        for chunk in split_message(text):
            if markdown:
                try:
                    await self._telegram_request(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": markdown_to_telegram_html(chunk),
                            "parse_mode": "HTML",
                            "link_preview_options": {"is_disabled": True},
                        },
                    )
                    continue
                except RuntimeError as err:
                    _LOGGER.debug("HTML message rejected, sending plain: %s", err)
            await self._telegram_request(
                "sendMessage", {"chat_id": chat_id, "text": chunk}
            )
