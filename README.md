# AI Coach for Home Assistant

A personal AI fitness coach as a Home Assistant custom integration. All data is
stored per Home Assistant user in a local SQLite database (`/config/ai_coach.db`).

## How the coach remembers

The coach does not re-read the whole chat. Each reply only receives the last
few messages (max 8, from the last 12 hours) plus a compact summary built from
structured data that the LLM stores itself through function calls:

| Tool | Stored in | Example |
| --- | --- | --- |
| `log_meal` | `meals` (with estimated kcal/macros) | "I had pasta carbonara for lunch" |
| `log_training` | `training_sessions` | "Ran 8 km in 45 min this morning" |
| `log_weight` | `weight_entries` | "I weigh 82.4 kg today" |
| `log_wellbeing` | `wellbeing_entries` | "Slept badly, feeling tired" |
| `save_plan` | `plans` (one active meal and one active training plan) | Agreed meal plan / running plan |
| `remember` / `forget` | `coach_notes` (optionally expiring) | "BBQ tonight" (until end of day), "allergic to nuts" |
| `get_logs` | – | Looks up older data on demand |

Chat history is only kept for display (last 200 messages per user).

## Installation (HACS)

1. HACS → Integrations → ⋮ → *Custom repositories* → add this repo as type *Integration*.
2. Install **AI Coach** and restart Home Assistant.
3. Settings → Devices & services → *Add integration* → **AI Coach**.
4. Add the card to a dashboard:

```yaml
type: custom:ai-coach-card
title: AI Coach
height: 400px
```

The card JavaScript is served and registered by the integration itself, so no
manual Lovelace resource is needed.

## Configuration

| Field | Description |
| --- | --- |
| LLM provider | Google AI Studio or OpenAI |
| LLM API key | API key for the selected provider |
| Model | Provider-specific dropdown; changeable later via *Configure* |
| Telegram bot token | Optional, from @BotFather |
| Coach style | gentle / balanced / strict / drill sergeant (changeable later) |

## Telegram linking

Select **Link Telegram** in the dashboard card, then send `/link 123456` to
the configured bot using the generated six-digit code. The code is bound to
the currently authenticated Home Assistant user. Dashboard and Telegram
messages then share that user's chat history and onboarding profile. Once
linked, the link bar disappears and a Telegram icon in the card header can be
used to unlink. Long replies are split into multiple Telegram messages.

## WebSocket API

| Command | Payload | Result |
| --- | --- | --- |
| `ai_coach/history` | `limit?` | `{messages: [...]}` |
| `ai_coach/send_message` | `message` | `{user_message, assistant_message}` |
| `ai_coach/clear_history` | – | `{deleted}` |
| `ai_coach/generate_pairing_code` | – | `{pairing_code}` |
| `ai_coach/status` | – | `{telegram_configured, telegram_linked}` |
| `ai_coach/unlink_telegram` | – | `{unlinked}` |

The user is always derived from the authenticated WebSocket connection.
