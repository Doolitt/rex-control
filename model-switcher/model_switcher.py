#!/usr/bin/env python3
"""
model_switcher.py — OpenClaw model switching utility.

Commands:
  current                          Print the active model
  validate <modelOrAlias>          Check if model exists on OpenRouter
  set <modelOrAlias> [--dry-run]   Switch model with restart + healthcheck + rollback
  rollback <backup_file>           Restore a specific backup
"""
import json, os, sys, time, subprocess, re
from pathlib import Path

CONFIG    = Path("/root/.openclaw/openclaw.json")
BACKUP_DIR = Path("/root/.openclaw/backups")
ALIAS_PATH = Path(__file__).with_name("model_aliases.json")
LOG_DIR    = Path("/tmp/openclaw")


# ── helpers ─────────────────────────────────────────────────────────────────

def sh(cmd, check=True, capture=True, timeout=30):
    r = subprocess.run(
        cmd, check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True, timeout=timeout,
    )
    return r.stdout or ""


def load_aliases() -> dict:
    return json.loads(ALIAS_PATH.read_text()) if ALIAS_PATH.exists() else {}


def normalize(token: str) -> str:
    """Strip accidental openrouter/ prefix, then resolve alias."""
    t = token.strip()
    if t.lower().startswith("openrouter/"):
        t = t[len("openrouter/"):]
    aliases = load_aliases()
    return aliases.get(t, t)


def openrouter_has_model(model_id: str) -> bool:
    """Return True if model_id appears in the OpenRouter catalog."""
    try:
        out = sh(["curl", "-sf", "https://openrouter.ai/api/v1/models"], timeout=20)
        data = json.loads(out)
        ids = {m.get("id") for m in data.get("data", []) if m.get("id")}
        return model_id in ids
    except Exception:
        return False


def backup_config() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = str(int(time.time()))
    dst = BACKUP_DIR / f"openclaw.json.{ts}.bak"
    dst.write_text(CONFIG.read_text())
    return dst


def patch_config(model_id: str):
    """Write model_id into agents.defaults.model.primary and main agent."""
    cfg = json.loads(CONFIG.read_text())
    cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})
    cfg["agents"]["defaults"]["model"]["primary"] = model_id

    for agent in cfg["agents"].get("list", []):
        if agent.get("id") == "main":
            agent.setdefault("model", {})["primary"] = model_id

    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


# ── log-based healthcheck ────────────────────────────────────────────────────

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
    """Restart gateway, then verify 'agent model: <id>' appears in new log lines."""
    pre_size = _log_size()
    sh(["systemctl", "--user", "restart", "openclaw-gateway"], timeout=30)

    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(2)
        new_text = _read_log_from(pre_size)

        # Also check journalctl for fast boot messages
        jctl = sh(
            ["journalctl", "--user", "-u", "openclaw-gateway",
             "--since", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 25)),
             "--no-pager", "-n", "100"],
            check=False,
        )

        combined = new_text + jctl

        if re.search(r"Unknown model:|model not found|404.*model", combined, re.IGNORECASE):
            return False, "Unknown/invalid model detected in logs after restart."

        # Match "agent model: <id>" anywhere in new log output
        if f"agent model: {model_id}" in combined:
            return True, f"Confirmed: agent model: {model_id}"

    # Timed out — check if gateway is still running
    status = sh(["systemctl", "--user", "is-active", "openclaw-gateway"], check=False).strip()
    if status == "active":
        return False, f"Gateway is running but 'agent model: {model_id}' never appeared in logs (20s timeout)."
    return False, f"Gateway failed to start (status: {status})."


# ── subcommands ──────────────────────────────────────────────────────────────

def cmd_current():
    cfg = json.loads(CONFIG.read_text())
    # Prefer main agent's model, fall back to defaults
    for agent in cfg.get("agents", {}).get("list", []):
        if agent.get("id") == "main":
            m = agent.get("model", {}).get("primary")
            if m:
                print(m)
                return
    m = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "(unknown)")
    print(m)


def cmd_validate(raw: str):
    model_id = normalize(raw)
    print(f"Checking OpenRouter for: {model_id}")
    if openrouter_has_model(model_id):
        print(f"✅ Valid: {model_id} exists on OpenRouter.")
        sys.exit(0)
    else:
        print(f"❌ Invalid: {model_id} not found in OpenRouter catalog.")
        sys.exit(2)


def cmd_set(raw: str, dry_run: bool = False):
    model_id = normalize(raw)
    print(f"Resolved model: {model_id}")

    # 1. Validate
    print("Validating on OpenRouter…")
    if not openrouter_has_model(model_id):
        print(f"❌ {model_id} not found on OpenRouter. Aborting.")
        sys.exit(2)
    print("✅ Model confirmed on OpenRouter.")

    if dry_run:
        print(f"🔍 DRY RUN — would switch to: {model_id} (no changes made)")
        return

    # 2. Backup
    backup = backup_config()
    print(f"💾 Config backed up to: {backup}")

    # 3. Patch config
    patch_config(model_id)
    print(f"📝 Config updated with model: {model_id}")

    # 4. Restart + healthcheck
    print("🔄 Restarting openclaw-gateway…")
    ok, msg = restart_and_check(model_id)
    if ok:
        print(f"✅ {msg}")
        print(f"🎉 Successfully switched to: {model_id}")
        return

    # 5. Rollback
    print(f"❌ Healthcheck failed: {msg}")
    print(f"⏪ Rolling back to: {backup}")
    CONFIG.write_text(Path(backup).read_text())
    sh(["systemctl", "--user", "restart", "openclaw-gateway"], timeout=30)
    time.sleep(3)
    print("🔄 Gateway restarted with previous config.")
    print(f"❌ Set failed — rolled back to backup: {backup}")
    sys.exit(3)


def cmd_rollback(backup_path: str):
    b = Path(backup_path)
    if not b.exists():
        print(f"❌ Backup not found: {backup_path}")
        sys.exit(2)
    CONFIG.write_text(b.read_text())
    sh(["systemctl", "--user", "restart", "openclaw-gateway"], timeout=30)
    time.sleep(3)
    print(f"✅ Rolled back to: {b}")


# ── entry point ──────────────────────────────────────────────────────────────

def usage():
    print(__doc__)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        usage()

    cmd = args[0].lower()

    if cmd == "current":
        cmd_current()

    elif cmd == "validate":
        if len(args) < 2:
            print("Usage: model_switcher.py validate <modelOrAlias>")
            sys.exit(1)
        cmd_validate(args[1])

    elif cmd == "set":
        if len(args) < 2:
            print("Usage: model_switcher.py set <modelOrAlias> [--dry-run]")
            sys.exit(1)
        cmd_set(args[1], dry_run="--dry-run" in args)

    elif cmd == "rollback":
        if len(args) < 2:
            print("Usage: model_switcher.py rollback <backup_file>")
            sys.exit(1)
        cmd_rollback(args[1])

    else:
        usage()


if __name__ == "__main__":
    main()
