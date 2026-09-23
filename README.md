# AI Coach for Home Assistant

A personal AI fitness coach as a Home Assistant custom integration. Chat history,
weight and training data are stored per Home Assistant user in a local SQLite
database (`/config/ai_coach.db`).

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
| LLM API key | Key for any OpenAI-compatible chat completions API |
| LLM API base URL | Defaults to `https://api.openai.com/v1` |
| Model | Changeable later via *Configure* |
| Telegram bot token | Optional, from @BotFather |
| Coach style | gentle / balanced / strict / drill sergeant (changeable later) |

## WebSocket API

| Command | Payload | Result |
| --- | --- | --- |
| `ai_coach/history` | `limit?` | `{messages: [...]}` |
| `ai_coach/send_message` | `message` | `{user_message, assistant_message}` |
| `ai_coach/clear_history` | – | `{deleted}` |

The user is always derived from the authenticated WebSocket connection.
