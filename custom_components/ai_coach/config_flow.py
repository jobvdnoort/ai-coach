"""Config flow for AI Coach."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    COACH_STYLES,
    CONF_BASE_URL,
    CONF_COACH_STYLE,
    CONF_MODEL,
    CONF_TELEGRAM_BOT_TOKEN,
    DEFAULT_BASE_URL,
    DEFAULT_COACH_STYLE,
    DEFAULT_MODEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

VALIDATION_TIMEOUT = aiohttp.ClientTimeout(total=15)

COACH_STYLE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=COACH_STYLES,
        translation_key=CONF_COACH_STYLE,
        mode=SelectSelectorMode.DROPDOWN,
    )
)
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


class CannotConnect(Exception):
    """Service could not be reached."""


class InvalidAuth(Exception):
    """LLM API key was rejected."""


class InvalidTelegramToken(Exception):
    """Telegram rejected the bot token."""


async def _validate_llm(hass: HomeAssistant, api_key: str, base_url: str) -> None:
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=VALIDATION_TIMEOUT,
        ) as resp:
            if resp.status in (401, 403):
                raise InvalidAuth
            if resp.status >= 400:
                raise CannotConnect
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect from err


async def _validate_telegram(hass: HomeAssistant, token: str) -> str:
    """Validate the bot token and return the bot's username."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=VALIDATION_TIMEOUT
        ) as resp:
            if resp.status in (401, 404):
                raise InvalidTelegramToken
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect from err
    if not data.get("ok"):
        raise InvalidTelegramToken
    return data["result"].get("username", "")


class AICoachConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of AI Coach."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            telegram_token = user_input.get(CONF_TELEGRAM_BOT_TOKEN, "").strip()
            try:
                await _validate_llm(
                    self.hass, user_input[CONF_API_KEY], user_input[CONF_BASE_URL]
                )
                if telegram_token:
                    await _validate_telegram(self.hass, telegram_token)
            except InvalidAuth:
                errors[CONF_API_KEY] = "invalid_auth"
            except InvalidTelegramToken:
                errors[CONF_TELEGRAM_BOT_TOKEN] = "invalid_telegram_token"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during AI Coach setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="AI Coach",
                    data={
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_BASE_URL: user_input[CONF_BASE_URL],
                        CONF_TELEGRAM_BOT_TOKEN: telegram_token,
                    },
                    options={
                        CONF_COACH_STYLE: user_input[CONF_COACH_STYLE],
                        CONF_MODEL: user_input[CONF_MODEL],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): PASSWORD_SELECTOR,
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): URL_SELECTOR,
                vol.Required(CONF_MODEL, default=DEFAULT_MODEL): str,
                vol.Optional(CONF_TELEGRAM_BOT_TOKEN, default=""): PASSWORD_SELECTOR,
                vol.Required(
                    CONF_COACH_STYLE, default=DEFAULT_COACH_STYLE
                ): COACH_STYLE_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AICoachOptionsFlow()


class AICoachOptionsFlow(OptionsFlow):
    """Change coach style and model after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COACH_STYLE,
                        default=options.get(CONF_COACH_STYLE, DEFAULT_COACH_STYLE),
                    ): COACH_STYLE_SELECTOR,
                    vol.Required(
                        CONF_MODEL, default=options.get(CONF_MODEL, DEFAULT_MODEL)
                    ): str,
                }
            ),
        )
