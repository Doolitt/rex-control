---
name: model-switcher
description: >
  Switch, validate, and inspect the active AI model for the OpenClaw gateway via Discord commands.
  Trigger when a user sends: `!model current`, `!model validate <model>`, or `!model set <model>`.
  Also triggers on variations like "what model are you running", "switch model to X", "change model".
  Handles OpenRouter model IDs and short aliases (sonnet, opus, deepseek).
---

# model-switcher

Handles `!model` Discord commands by calling `model_switcher.py`.

## Script location

```
/root/.openclaw/workspace/skills/model-switcher/model_switcher.py
```

## Aliases (model_aliases.json)

| Alias     | Resolves to                        |
|-----------|------------------------------------|
| sonnet    | anthropic/claude-sonnet-4.6        |
| opus      | anthropic/claude-opus-4.6          |
| deepseek  | deepseek/deepseek-v3.2             |

`openrouter/<id>` prefixes are automatically stripped before validation.

## Commands

### `!model current`

Print the model currently configured for the main agent.

```bash
python3 /root/.openclaw/workspace/skills/model-switcher/model_switcher.py current
```

Format response as:
```
🤖 Current model: <model_id>
```

---

### `!model validate <modelOrAlias>`

Check whether a model exists in the OpenRouter catalog.

```bash
python3 /root/.openclaw/workspace/skills/model-switcher/model_switcher.py validate <modelOrAlias>
```

Relay the script's output verbatim (it already includes ✅/❌ emoji).

---

### `!model set <modelOrAlias>`

Switch the active model. This:
1. Validates the model on OpenRouter
2. Backs up `/root/.openclaw/openclaw.json`
3. Patches `agents.defaults.model.primary` and `agents.list[id=main].model.primary`
4. Restarts `openclaw-gateway` via systemctl
5. Verifies logs contain `agent model: <model_id>`
6. Rolls back on failure

```bash
python3 /root/.openclaw/workspace/skills/model-switcher/model_switcher.py set <modelOrAlias>
```

This takes ~10–25 seconds. Let the user know it's in progress before calling. Relay the full output.

⚠️ After a successful `set`, the gateway restarts and your session reconnects — the switch affects all future messages.

---

### `!model help`

Reply with this reference without calling the script:

```
🤖 Model Switcher Commands:
• !model current — show active model
• !model validate <model> — check if model exists on OpenRouter
• !model set <model> — switch model (restarts gateway)

Aliases: sonnet · opus · deepseek
Examples:
  !model set sonnet
  !model set deepseek/deepseek-v3.2
  !model validate anthropic/claude-opus-4.6
```

---

## Output formatting for Discord

- Use emoji prefixes (✅ ❌ 🔄 💾 🎉) as the script provides them
- Wrap multi-line output in a code block if >3 lines
- For `set`, send a "working on it" message first, then the result when done
- Never expose the raw backup file path unless specifically asked
