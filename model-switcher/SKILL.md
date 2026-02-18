---
name: model-switcher
description: >
  Provider-aware model switching for OpenClaw via Discord slash command handlers.
  Use for `/models` or `/model` style UX (or fallback aliases `/modelsx` + `/modelx`),
  including local model browsing, alias resolution, safe switching, and rollback.
---

# model-switcher

Handles model browsing and switching with **local registry-first** behavior (no listing API calls).

## Script location

```
/root/.openclaw/workspace/skills/model-switcher/model_switcher.py
```

## Registry files

- `models.json` — provider + model catalog used for listing and local validation.
- `model_aliases.json` — optional compatibility aliases merged into registry aliases.

## Recommended slash-command mapping

If `/model` and `/models` are already owned by OpenClaw core, map this skill to `/modelx` and `/modelsx` with the same args.

- `/models` → `python3 .../model_switcher.py models [provider] [--page N] [--page-size N]`
- `/model` → `python3 .../model_switcher.py model`
- `/model set <modelOrAlias>` → `python3 .../model_switcher.py model set <modelOrAlias> --user-id <discordUserId>`
- `/model reset` → `python3 .../model_switcher.py model reset --user-id <discordUserId>`
- `/model add <provider> <id> [aliases...]` → `python3 .../model_switcher.py model add ... --user-id <discordUserId>`
- `/model remove <provider> <id>` → `python3 .../model_switcher.py model remove ... --user-id <discordUserId>`
- `/model export` → `python3 .../model_switcher.py model export --user-id <discordUserId>`
- `/model reload` → `python3 .../model_switcher.py model reload --user-id <discordUserId>`

Mutating commands require `--user-id` that exists in `channels.discord.allowFrom`.

## Validation modes

Pass `--validate-mode` when needed:

- `local` (default): local registry only (cheap; no API calls)
- `remote`: local + provider-aware remote check (`openrouter/*` checks OpenRouter catalog)
- `none`: skip validation

## Safe switching behavior

`model set` and `model reset`:

1. Resolve alias to canonical provider-qualified model id.
2. Validate (default local only).
3. Backup `/root/.openclaw/openclaw.json` to `/root/.openclaw/backups/openclaw.json.<ts>.bak`.
4. Patch:
   - `agents.defaults.model.primary`
   - `agents.list[id=main].model.primary`
5. Restart `openclaw-gateway` (`systemctl --user restart openclaw-gateway`).
6. Health-check:
   - systemd active status
   - logs contain `agent model: <expected>`
   - fail fast on `Unknown model` / `model not found`
7. Roll back to backup and restart if health-check fails.

## Quick run instructions

1. Restart gateway after skill updates:
   ```bash
   systemctl --user restart openclaw-gateway
   ```
2. Sanity checks:
   ```bash
   python3 /root/.openclaw/workspace/skills/model-switcher/model_switcher.py models
   python3 /root/.openclaw/workspace/skills/model-switcher/model_switcher.py model
   python3 /root/.openclaw/workspace/skills/model-switcher/model_switcher.py model set deepseek --dry-run --user-id 790975088227778581
   ```
