# Discord Model Switcher (rex-control)

This module provides Discord message commands to safely switch OpenClaw models with provider-aware IDs, authorization checks, and rollback history.

## Features

- Provider-aware model IDs: `openrouter/...`, `anthropic/...`, `openai/...`
- Independent Discord authorization for `/model ...` commands via admin user IDs and role IDs
- Persisted rollback stack at `/root/.openclaw/state/rex-control-model-history.json`
- Automatic rollback if apply fails with unknown model / gateway 404
- Atomic writes for local state/config fallback write path
- Keeps `agents.defaults.model.fallbacks` intact

## Commands

- `/model show`
- `/model list`
- `/model set <model_id>`
- `/model rollback`
- `/model rollback <N>`
- `/model pin-good`
- `/model revert-good`

## Authorization

Set one or both environment variables:

- `ADMIN_USER_IDS` (comma-separated Discord user IDs)
- `ADMIN_ROLE_IDS` (comma-separated Discord role IDs)

Examples:

```bash
export ADMIN_USER_IDS="123456789012345678,222233334444555566"
export ADMIN_ROLE_IDS="987654321098765432"
```

If neither variable is set, all `/model` commands are denied.

## Gateway / Config behavior

Primary path uses OpenClaw gateway RPC:

- `config.get`
- `config.patch`

Default endpoint: `http://127.0.0.1:18789/rpc` with optional bearer token from:

- `OPENCLAW_GATEWAY_TOKEN` or `OPENCLAW_GATEWAY_AUTH_TOKEN`

Fallback path (if gateway patch is unavailable):

- read config from `/root/.openclaw/openclaw.json`
- apply model update preserving fallbacks
- validate JSON
- write atomically (temp + rename)

## State file

History file (default):

- `/root/.openclaw/state/rex-control-model-history.json`

Schema:

```json
{
  "stack": ["openrouter/anthropic/claude-sonnet-4.6"],
  "lastKnownGood": "openrouter/anthropic/claude-sonnet-4.6"
}
```

Stack max length is 10.

## Supported model allowlist

Hard-coded allowlist lives in:

- `model-switcher/supported-models.json`

Includes required IDs:

- `openrouter/anthropic/claude-sonnet-4.6`
- `openrouter/anthropic/claude-opus-4.6`
- `openrouter/deepseek/deepseek-v3.2`
- `openrouter/qwen/qwen3.5-plus-02-15`

## OpenRouter validation

For `openrouter/*` IDs, remote validation against `https://openrouter.ai/api/v1/models` is attempted when `OPENROUTER_API_KEY` is set. Without key, allowlist-only validation is used.

## Integration sketch

```js
const { DiscordModelSwitcher } = require('./model-switcher');

const switcher = new DiscordModelSwitcher();

async function onDiscordMessage(message) {
  await switcher.handleDiscordMessage({
    content: message.content,
    author: { id: message.author.id },
    member: { roles: message.member?.roles?.cache?.map((r) => r.id) || [] },
    reply: (text) => message.reply(text)
  });
}
```

