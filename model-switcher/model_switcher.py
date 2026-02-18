#!/usr/bin/env python3
"""
model_switcher.py — OpenClaw model switching utility.

Primary command families (for Discord slash command mapping):
  models [provider] [--page N] [--page-size N] [--json]
  model [show]
  model set <modelOrAlias> [--dry-run] [--validate-mode local|remote|none]
  model reset [--dry-run]
  model add <provider> <id> [aliases...]
  model remove <provider> <id>
  model export
  model reload
  model help

Compatibility commands:
  current
  validate <modelOrAlias> [--validate-mode local|remote|none]
  set <modelOrAlias> [--dry-run] [--validate-mode local|remote|none]
  rollback <backup_file>
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CONFIG = Path("/root/.openclaw/openclaw.json")
BACKUP_DIR = Path("/root/.openclaw/backups")
MODELS_PATH = Path(__file__).with_name("models.json")
ALIAS_PATH = Path(__file__).with_name("model_aliases.json")
LOG_DIR = Path("/tmp/openclaw")
DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


def sh(cmd, check=True, capture=True, timeout=30):
    r = subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
        timeout=timeout,
    )
    return r.stdout or ""


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG.read_text())


def load_models() -> dict[str, Any]:
    if not MODELS_PATH.exists():
        return {"providers": {}}
    return json.loads(MODELS_PATH.read_text())


def save_models(registry: dict[str, Any]) -> None:
    MODELS_PATH.write_text(json.dumps(registry, indent=2) + "\n")


def load_aliases() -> dict[str, str]:
    if ALIAS_PATH.exists():
        try:
            data = json.loads(ALIAS_PATH.read_text())
            return {str(k).strip().lower(): str(v).strip() for k, v in data.items()}
        except Exception:
            return {}
    return {}


def provider_from_model(model_id: str) -> str | None:
    parts = model_id.split("/")
    return parts[0].lower() if parts else None


def build_alias_index(registry: dict[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for provider, pdata in registry.get("providers", {}).items():
        for item in pdata.get("models", []):
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            aliases[model_id.lower()] = model_id
            base = model_id.split("/")[-1].lower()
            aliases.setdefault(base, model_id)
            for a in item.get("aliases", []):
                a_norm = str(a).strip().lower()
                if a_norm:
                    aliases[a_norm] = model_id
            if provider == "anthropic":
                dotted = model_id.replace("-4-6", "-4.6")
                dashed = model_id.replace("-4.6", "-4-6")
                aliases[dotted.lower()] = model_id
                aliases[dashed.lower()] = model_id
    aliases.update(load_aliases())
    return aliases


def normalize_model(raw: str, registry: dict[str, Any]) -> str:
    token = raw.strip()
    if not token:
        return token
    alias_index = build_alias_index(registry)
    resolved = alias_index.get(token.lower(), token)

    # Legacy behavior: openrouter IDs without prefix map to openrouter/<id>
    if "/" in resolved and not resolved.lower().startswith(("anthropic/", "openrouter/", "openai/", "google/", "xai/", "deepseek/")):
        resolved = f"openrouter/{resolved}"

    if resolved.startswith("anthropic/"):
        name = resolved.split("/", 1)[1]
        if "4.6" in name:
            resolved = f"anthropic/{name.replace('4.6', '4-6')}"
    return resolved


def local_model_exists(model_id: str, registry: dict[str, Any]) -> bool:
    alias_index = build_alias_index(registry)
    return model_id.lower() in alias_index


def openrouter_has_model(model_id: str) -> bool:
    target = model_id
    if model_id.startswith("openrouter/"):
        target = model_id[len("openrouter/") :]
    try:
        out = sh(["curl", "-sf", "https://openrouter.ai/api/v1/models"], timeout=20)
        data = json.loads(out)
        ids = {m.get("id") for m in data.get("data", []) if m.get("id")}
        return target in ids
    except Exception:
        return False


def remote_validate(model_id: str) -> bool:
    provider = provider_from_model(model_id)
    if provider == "openrouter":
        return openrouter_has_model(model_id)
    if provider == "anthropic":
        # Optional lightweight check could be added; avoid calls by default.
        return True
    return True


def validate_model(model_id: str, registry: dict[str, Any], mode: str) -> tuple[bool, str]:
    if mode == "none":
        return True, "Validation skipped (mode=none)."

    if mode in {"local", "remote"} and not local_model_exists(model_id, registry):
        return False, f"Model not found in local registry: {model_id}"

    if mode == "local":
        return True, f"Local registry validation passed: {model_id}"

    ok = remote_validate(model_id)
    if ok:
        return True, f"Remote validation passed: {model_id}"
    return False, f"Remote validation failed: {model_id}"


def backup_config() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = str(int(time.time()))
    dst = BACKUP_DIR / f"openclaw.json.{ts}.bak"
    dst.write_text(CONFIG.read_text())
    return dst


def current_model(cfg: dict[str, Any]) -> str:
    for agent in cfg.get("agents", {}).get("list", []):
        if agent.get("id") == "main":
            m = agent.get("model", {}).get("primary")
            if m:
                return m
    return cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "(unknown)")


def patch_config(model_id: str):
    cfg = load_config()
    cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})
    cfg["agents"]["defaults"]["model"]["primary"] = model_id

    if cfg["agents"].get("defaults", {}).get("imageModel") is not None:
        # Update only if existing and non-null.
        cfg["agents"]["defaults"].setdefault("imageModel", {})

    for agent in cfg["agents"].get("list", []):
        if agent.get("id") == "main":
            agent.setdefault("model", {})["primary"] = model_id

    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


def _today_log() -> Path:
    return LOG_DIR / f"openclaw-{time.strftime('%Y-%m-%d')}.log"


def _log_size() -> int:
    p = _today_log()
    return p.stat().st_size if p.exists() else 0


def _read_log_from(offset: int) -> str:
    p = _today_log()
    if not p.exists():
        return ""
    with open(p, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", errors="replace")


def restart_and_check(model_id: str) -> tuple[bool, str]:
    pre_size = _log_size()
    sh(["systemctl", "--user", "restart", "openclaw-gateway"], timeout=30)

    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(2)
        new_text = _read_log_from(pre_size)
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 40))
        jctl = sh(
            [
                "journalctl",
                "--user",
                "-u",
                "openclaw-gateway",
                "--since",
                since,
                "--no-pager",
                "-n",
                "200",
            ],
            check=False,
        )

        status = sh(["systemctl", "--user", "is-active", "openclaw-gateway"], check=False).strip()
        combined = new_text + "\n" + jctl

        if re.search(r"Unknown model:|model not found|404.*model", combined, re.IGNORECASE):
            return False, "Unknown/invalid model detected in logs after restart."
        if f"agent model: {model_id}" in combined:
            return True, f"Confirmed: agent model: {model_id}"
        if status != "active":
            return False, f"Gateway failed to stay active (status: {status})."

    return False, f"Gateway active but 'agent model: {model_id}' not seen within 25s."


def rollback_to(backup: Path):
    CONFIG.write_text(backup.read_text())
    sh(["systemctl", "--user", "restart", "openclaw-gateway"], timeout=30)


def require_allowlisted(user_id: str | None):
    if not user_id:
        print("❌ Missing --user-id for restricted command.")
        sys.exit(4)
    allow = (
        load_config()
        .get("channels", {})
        .get("discord", {})
        .get("allowFrom", [])
    )
    if str(user_id) not in {str(x) for x in allow}:
        print(f"❌ User {user_id} is not in channels.discord.allowFrom")
        sys.exit(4)


def cmd_models(args: list[str]):
    registry = load_models()
    provider = args[0].lower() if args else None
    page = 1
    page_size = 10
    as_json = "--json" in args

    if "--page" in args:
        i = args.index("--page")
        page = int(args[i + 1])
    if "--page-size" in args:
        i = args.index("--page-size")
        page_size = int(args[i + 1])

    providers = registry.get("providers", {})
    if as_json:
        print(json.dumps(registry, indent=2))
        return

    if not provider or provider.startswith("--"):
        for p, pdata in providers.items():
            print(f"{p}\t{pdata.get('label', p)}\t{len(pdata.get('models', []))}")
        return

    if provider not in providers:
        print(f"❌ Unknown provider: {provider}")
        sys.exit(2)

    models = providers[provider].get("models", [])
    total = len(models)
    start = max(0, (page - 1) * page_size)
    end = min(total, start + page_size)
    print(f"Provider: {provider} ({start+1}-{end} / {total})")
    for item in models[start:end]:
        alias_txt = ", ".join(item.get("aliases", []))
        print(f"- {item['id']}" + (f"  [{alias_txt}]" if alias_txt else ""))


def cmd_model_show():
    cfg = load_config()
    print(f"🤖 Current model: {current_model(cfg)}")


def cmd_model_set(model_raw: str, dry_run: bool, validate_mode: str):
    registry = load_models()
    model_id = normalize_model(model_raw, registry)
    print(f"Resolved model: {model_id}")

    ok, msg = validate_model(model_id, registry, validate_mode)
    if not ok:
        print(f"❌ {msg}")
        sys.exit(2)
    print(f"✅ {msg}")

    if dry_run:
        print(f"🔍 DRY RUN — would switch to: {model_id}")
        return

    backup = backup_config()
    print(f"💾 Config backed up to: {backup}")

    patch_config(model_id)
    print(f"📝 Config updated with model: {model_id}")

    print("🔄 Restarting openclaw-gateway…")
    ok, health = restart_and_check(model_id)
    if ok:
        print(f"✅ {health}")
        print(f"🎉 Successfully switched to: {model_id}")
        return

    print(f"❌ Healthcheck failed: {health}")
    print(f"⏪ Rolling back to: {backup}")
    rollback_to(backup)
    print("🔄 Gateway restarted with previous config.")
    print(f"❌ Set failed — rolled back to backup: {backup}")
    sys.exit(3)


def cmd_model_reset(dry_run: bool, validate_mode: str):
    cmd_model_set(DEFAULT_MODEL, dry_run=dry_run, validate_mode=validate_mode)


def cmd_model_add(provider: str, model_id: str, aliases: list[str]):
    registry = load_models()
    providers = registry.setdefault("providers", {})
    pdata = providers.setdefault(provider, {"label": provider.title(), "models": []})

    models = pdata.setdefault("models", [])
    for item in models:
        if item.get("id") == model_id:
            existing = set(item.get("aliases", []))
            existing.update(aliases)
            item["aliases"] = sorted(existing)
            save_models(registry)
            print(f"✅ Updated aliases for existing model: {model_id}")
            return

    models.append({"id": model_id, "aliases": aliases})
    save_models(registry)
    print(f"✅ Added {model_id} under provider {provider}")


def cmd_model_remove(provider: str, model_id: str):
    registry = load_models()
    providers = registry.get("providers", {})
    if provider not in providers:
        print(f"❌ Unknown provider: {provider}")
        sys.exit(2)

    models = providers[provider].get("models", [])
    new_models = [m for m in models if m.get("id") != model_id]
    if len(new_models) == len(models):
        print(f"❌ Model not found: {model_id}")
        sys.exit(2)

    providers[provider]["models"] = new_models
    save_models(registry)
    print(f"✅ Removed {model_id} from provider {provider}")


def cmd_model_export():
    print(MODELS_PATH.read_text())


def cmd_model_reload():
    registry = load_models()
    providers = registry.get("providers", {})
    count = sum(len(p.get("models", [])) for p in providers.values())
    print(f"✅ Reloaded models.json ({len(providers)} providers, {count} models)")


def cmd_validate(model_raw: str, validate_mode: str):
    registry = load_models()
    model_id = normalize_model(model_raw, registry)
    print(f"Resolved model: {model_id}")
    ok, msg = validate_model(model_id, registry, validate_mode)
    if ok:
        print(f"✅ {msg}")
        return
    print(f"❌ {msg}")
    sys.exit(2)


def usage():
    print(__doc__)
    sys.exit(1)


def _flag_value(args: list[str], name: str, default: str | None = None) -> str | None:
    if name not in args:
        return default
    idx = args.index(name)
    if idx + 1 >= len(args):
        return default
    return args[idx + 1]


def main():
    args = sys.argv[1:]
    if not args:
        usage()

    user_id = _flag_value(args, "--user-id")
    if "--user-id" in args:
        idx = args.index("--user-id")
        del args[idx:idx + 2]

    validate_mode = _flag_value(args, "--validate-mode", "local")
    if validate_mode not in {"local", "remote", "none"}:
        print("❌ --validate-mode must be local|remote|none")
        sys.exit(1)
    if "--validate-mode" in args:
        idx = args.index("--validate-mode")
        del args[idx:idx + 2]

    cmd = args[0].lower()

    if cmd == "models":
        cmd_models(args[1:])
        return

    if cmd == "model":
        if len(args) == 1 or args[1] in {"show", "current"}:
            cmd_model_show()
            return
        sub = args[1].lower()
        if sub == "set" and len(args) >= 3:
            require_allowlisted(user_id)
            cmd_model_set(args[2], dry_run="--dry-run" in args, validate_mode=validate_mode)
            return
        if sub == "reset":
            require_allowlisted(user_id)
            cmd_model_reset(dry_run="--dry-run" in args, validate_mode=validate_mode)
            return
        if sub == "add" and len(args) >= 4:
            require_allowlisted(user_id)
            cmd_model_add(args[2].lower(), args[3], args[4:])
            return
        if sub == "remove" and len(args) >= 4:
            require_allowlisted(user_id)
            cmd_model_remove(args[2].lower(), args[3])
            return
        if sub == "export":
            require_allowlisted(user_id)
            cmd_model_export()
            return
        if sub == "reload":
            require_allowlisted(user_id)
            cmd_model_reload()
            return
        if sub == "help":
            usage()
        usage()

    # Compatibility paths
    if cmd == "current":
        cmd_model_show()
        return
    if cmd == "validate" and len(args) >= 2:
        cmd_validate(args[1], validate_mode=validate_mode)
        return
    if cmd == "set" and len(args) >= 2:
        require_allowlisted(user_id)
        cmd_model_set(args[1], dry_run="--dry-run" in args, validate_mode=validate_mode)
        return
    if cmd == "rollback" and len(args) >= 2:
        require_allowlisted(user_id)
        b = Path(args[1])
        if not b.exists():
            print(f"❌ Backup not found: {b}")
            sys.exit(2)
        rollback_to(b)
        print(f"✅ Rolled back to: {b}")
        return

    usage()


if __name__ == "__main__":
    main()
