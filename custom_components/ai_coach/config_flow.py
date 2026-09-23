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
    BooleanSelector,
    BooleanSelectorConfig,
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
    CONF_CUSTOM_MODEL,
    CONF_MODEL,
    CONF_PROVIDER,
    CONF_TELEGRAM_BOT_TOKEN,
    CONF_USE_CUSTOM_MODEL,
    DEFAULT_COACH_STYLE,
    DEFAULT_PROVIDER,
    DOMAIN,
    LLM_PROVIDERS,
    PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS,
    PROVIDER_GOOGLE_AI_STUDIO,
    PROVIDER_MODELS,
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
PROVIDER_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=LLM_PROVIDERS,
        translation_key=CONF_PROVIDER,
        mode=SelectSelectorMode.DROPDOWN,
    )
)
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
MODEL_TEXT_SELECTOR = TextSelector(TextSelectorConfig())
BOOLEAN_SELECTOR = BooleanSelector(BooleanSelectorConfig())


def _model_selector(provider: str) -> SelectSelector:
    """Create a model dropdown containing only models for the provider."""
    return SelectSelector(
        SelectSelectorConfig(
            options=PROVIDER_MODELS[provider],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _entry_provider(config_entry: ConfigEntry) -> str:
    """Return the provider, including for entries created before this field."""
    provider = config_entry.data.get(CONF_PROVIDER)
    if provider in LLM_PROVIDERS:
        return provider
    if "generativelanguage.googleapis.com" in config_entry.data.get(
        CONF_BASE_URL, ""
    ):
        return PROVIDER_GOOGLE_AI_STUDIO
    return DEFAULT_PROVIDER


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

    def __init__(self) -> None:
        self._setup_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            telegram_token = user_input.get(CONF_TELEGRAM_BOT_TOKEN, "").strip()
            provider = user_input[CONF_PROVIDER]
            base_url = PROVIDER_BASE_URLS[provider]
            try:
                await _validate_llm(self.hass, user_input[CONF_API_KEY], base_url)
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
                self._setup_data = {
                    CONF_API_KEY: user_input[CONF_API_KEY],
                    CONF_PROVIDER: provider,
                    CONF_BASE_URL: base_url,
                    CONF_TELEGRAM_BOT_TOKEN: telegram_token,
                    CONF_COACH_STYLE: user_input[CONF_COACH_STYLE],
                }
                return await self.async_step_model()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PROVIDER, default=DEFAULT_PROVIDER
                ): PROVIDER_SELECTOR,
                vol.Required(CONF_API_KEY): PASSWORD_SELECTOR,
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

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a model from the selected provider's supported models."""
        provider = self._setup_data[CONF_PROVIDER]
        if user_input is not None:
            if user_input[CONF_USE_CUSTOM_MODEL]:
                return await self.async_step_custom_model()
            return self._create_entry(user_input[CONF_MODEL], False)

        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USE_CUSTOM_MODEL, default=False
                    ): BOOLEAN_SELECTOR,
                    vol.Required(
                        CONF_MODEL,
                        default=PROVIDER_DEFAULT_MODELS[provider],
                    ): _model_selector(provider)
                }
            ),
        )

    async def async_step_custom_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow an arbitrary provider model ID."""
        errors: dict[str, str] = {}
        if user_input is not None:
            model = user_input[CONF_CUSTOM_MODEL].strip()
            if model:
                return self._create_entry(model, True)
            errors[CONF_CUSTOM_MODEL] = "model_required"

        return self.async_show_form(
            step_id="custom_model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CUSTOM_MODEL): MODEL_TEXT_SELECTOR,
                }
            ),
            errors=errors,
        )

    def _create_entry(
        self, model: str, use_custom_model: bool
    ) -> ConfigFlowResult:
        """Create the config entry after model selection."""
        return self.async_create_entry(
            title="AI Coach",
            data={
                CONF_API_KEY: self._setup_data[CONF_API_KEY],
                CONF_PROVIDER: self._setup_data[CONF_PROVIDER],
                CONF_BASE_URL: self._setup_data[CONF_BASE_URL],
                CONF_TELEGRAM_BOT_TOKEN: self._setup_data[
                    CONF_TELEGRAM_BOT_TOKEN
                ],
            },
            options={
                CONF_COACH_STYLE: self._setup_data[CONF_COACH_STYLE],
                CONF_MODEL: model.strip(),
                CONF_USE_CUSTOM_MODEL: use_custom_model,
            },
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
        options = self.config_entry.options
        provider = _entry_provider(self.config_entry)
        configured_model = options.get(
            CONF_MODEL, PROVIDER_DEFAULT_MODELS[provider]
        )
        model_is_custom = options.get(
            CONF_USE_CUSTOM_MODEL,
            configured_model not in PROVIDER_MODELS[provider],
        )

        if user_input is not None:
            if user_input[CONF_USE_CUSTOM_MODEL]:
                self._pending_coach_style = user_input[CONF_COACH_STYLE]
                self._custom_model_default = configured_model
                return await self.async_step_custom_model()
            return self.async_create_entry(
                data={
                    CONF_COACH_STYLE: user_input[CONF_COACH_STYLE],
                    CONF_MODEL: user_input[CONF_MODEL],
                    CONF_USE_CUSTOM_MODEL: False,
                }
            )

        dropdown_model = configured_model
        if dropdown_model not in PROVIDER_MODELS[provider]:
            dropdown_model = PROVIDER_DEFAULT_MODELS[provider]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COACH_STYLE,
                        default=options.get(CONF_COACH_STYLE, DEFAULT_COACH_STYLE),
                    ): COACH_STYLE_SELECTOR,
                    vol.Required(
                        CONF_USE_CUSTOM_MODEL, default=model_is_custom
                    ): BOOLEAN_SELECTOR,
                    vol.Required(
                        CONF_MODEL, default=dropdown_model
                    ): _model_selector(provider),
                }
            ),
        )

    async def async_step_custom_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set an arbitrary model ID in the options flow."""
        errors: dict[str, str] = {}
        if user_input is not None:
            model = user_input[CONF_CUSTOM_MODEL].strip()
            if model:
                return self.async_create_entry(
                    data={
                        CONF_COACH_STYLE: self._pending_coach_style,
                        CONF_MODEL: model,
                        CONF_USE_CUSTOM_MODEL: True,
                    }
                )
            errors[CONF_CUSTOM_MODEL] = "model_required"

        return self.async_show_form(
            step_id="custom_model",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CUSTOM_MODEL,
                        default=self._custom_model_default,
                    ): MODEL_TEXT_SELECTOR,
                }
            ),
            errors=errors,
        )
