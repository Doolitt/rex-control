const fs = require('node:fs/promises');
const path = require('node:path');

const DEFAULT_GATEWAY_URL = process.env.OPENCLAW_GATEWAY_URL || 'http://127.0.0.1:18789';
const DEFAULT_HISTORY_FILE =
  process.env.MODEL_HISTORY_FILE || '/root/.openclaw/state/rex-control-model-history.json';
const DEFAULT_CONFIG_FILE =
  process.env.OPENCLAW_CONFIG_FILE || '/root/.openclaw/openclaw.json';

const MODEL_PATTERN = /^(openrouter|anthropic|openai)\/.+/;
const MAX_STACK = 10;

class DiscordModelSwitcher {
  constructor(options = {}) {
    this.gatewayUrl = options.gatewayUrl || DEFAULT_GATEWAY_URL;
    this.gatewayToken = options.gatewayToken || process.env.OPENCLAW_GATEWAY_TOKEN || process.env.OPENCLAW_GATEWAY_AUTH_TOKEN;
    this.historyFile = options.historyFile || DEFAULT_HISTORY_FILE;
    this.configFile = options.configFile || DEFAULT_CONFIG_FILE;
    this.fetch = options.fetchImpl || global.fetch;
    this.allowedModels = new Set(options.allowedModels || loadSupportedModels(options.supportedModelsPath));
    this.adminUserIds = new Set(parseCsvIds(options.adminUserIds || process.env.ADMIN_USER_IDS));
    this.adminRoleIds = new Set(parseCsvIds(options.adminRoleIds || process.env.ADMIN_ROLE_IDS));

    if (!this.fetch) {
      throw new Error('fetch is required (Node 18+)');
    }
  }

  async handleDiscordMessage(ctx) {
    const raw = `${ctx?.content || ''}`.trim();
    if (!raw.startsWith('/model')) return false;

    if (!this.isAuthorized(ctx)) {
      await ctx.reply('⛔ You are not authorized to run /model commands.');
      return true;
    }

    const args = raw.split(/\s+/);
    const subcommand = args[1] || 'show';

    try {
      if (subcommand === 'show') {
        const current = await this.getCurrentModel();
        await ctx.reply(`Current model: ${current} (provider: ${providerFromModel(current)})`);
        return true;
      }

      if (subcommand === 'list') {
        await ctx.reply(`Supported models:\n${[...this.allowedModels].map((m) => `- ${m}`).join('\n')}`);
        return true;
      }

      if (subcommand === 'set') {
        const modelId = args[2];
        if (!modelId) {
          await ctx.reply('Usage: /model set <model_id>');
          return true;
        }

        await this.validateModelId(modelId);
        const previous = await this.getCurrentModel();
        await this.pushHistory(previous);

        try {
          await this.applyModel(modelId);
          await ctx.reply(`✅ Model updated to ${modelId}`);
        } catch (error) {
          await this.rollbackSteps(1, { keepHistory: false });
          await ctx.reply(`❌ Failed to apply model (${safeError(error)}). Rolled back to ${previous}.`);
        }
        return true;
      }

      if (subcommand === 'rollback') {
        const steps = Number.parseInt(args[2] || '1', 10);
        if (!Number.isInteger(steps) || steps <= 0) {
          await ctx.reply('Usage: /model rollback <N?> where N is a positive integer');
          return true;
        }
        const model = await this.rollbackSteps(steps, { keepHistory: false });
        await ctx.reply(`↩️ Rolled back ${steps} step(s). Current model: ${model}`);
        return true;
      }

      if (subcommand === 'pin-good') {
        const current = await this.getCurrentModel();
        const state = await this.readHistory();
        state.lastKnownGood = current;
        await this.writeHistory(state);
        await ctx.reply(`📌 Pinned last known good model: ${current}`);
        return true;
      }

      if (subcommand === 'revert-good') {
        const state = await this.readHistory();
        if (!state.lastKnownGood) {
          await ctx.reply('No last known good model is pinned yet.');
          return true;
        }
        const previous = await this.getCurrentModel();
        await this.pushHistory(previous);
        await this.applyModel(state.lastKnownGood);
        await ctx.reply(`✅ Reverted to last known good model: ${state.lastKnownGood}`);
        return true;
      }

      await ctx.reply('Unknown command. Available: show, set, rollback, pin-good, revert-good, list');
      return true;
    } catch (error) {
      await ctx.reply(`❌ ${safeError(error)}`);
      return true;
    }
  }

  isAuthorized(ctx) {
    const userId = `${ctx?.author?.id || ''}`;
    const roleIds = new Set((ctx?.member?.roles || []).map((id) => `${id}`));

    if (this.adminUserIds.size === 0 && this.adminRoleIds.size === 0) return false;
    if (this.adminUserIds.has(userId)) return true;
    for (const roleId of this.adminRoleIds) {
      if (roleIds.has(roleId)) return true;
    }
    return false;
  }

  async validateModelId(modelId) {
    if (!MODEL_PATTERN.test(modelId)) {
      throw new Error('Model ID must start with openrouter/, anthropic/, or openai/.');
    }

    if (!this.allowedModels.has(modelId)) {
      throw new Error('Model ID is not in supported-models.json allowlist.');
    }

    if (modelId.startsWith('openrouter/')) {
      await this.validateOpenRouterModel(modelId);
    }
  }

  async validateOpenRouterModel(modelId) {
    const key = process.env.OPENROUTER_API_KEY;
    if (!key) return;

    const response = await this.fetch('https://openrouter.ai/api/v1/models', {
      headers: {
        Authorization: `Bearer ${key}`
      }
    });

    if (!response.ok) {
      throw new Error(`OpenRouter model validation failed with HTTP ${response.status}`);
    }

    const payload = await response.json();
    const found = Array.isArray(payload?.data) && payload.data.some((entry) => entry?.id === modelId.replace(/^openrouter\//, ''));

    if (!found) {
      throw new Error(`OpenRouter model ${modelId} not found in /models listing`);
    }
  }

  async getCurrentModel() {
    const config = await this.getConfig();
    return (
      config?.agents?.list?.find((agent) => agent.id === 'main')?.model?.primary ||
      config?.agents?.defaults?.model?.primary
    );
  }

  async getConfig() {
    try {
      return await this.callGateway('config.get', {});
    } catch {
      const content = await fs.readFile(this.configFile, 'utf8');
      return JSON.parse(content);
    }
  }

  async applyModel(modelId) {
    const patch = {
      agents: {
        defaults: { model: { primary: modelId } }
      }
    };

    try {
      await this.callGateway('config.patch', { patch });
      return;
    } catch (error) {
      if (String(error.message || '').includes('404') || String(error.message || '').toLowerCase().includes('unknown model')) {
        throw error;
      }
    }

    const config = await this.getConfig();
    if (!config?.agents?.defaults?.model?.fallbacks) {
      throw new Error('Missing agents.defaults.model.fallbacks in config');
    }

    config.agents.defaults.model.primary = modelId;
    if (Array.isArray(config.agents.list)) {
      const mainAgent = config.agents.list.find((agent) => agent.id === 'main');
      if (mainAgent?.model?.primary) {
        mainAgent.model.primary = modelId;
      }
    }

    const json = JSON.stringify(config, null, 2);
    JSON.parse(json);
    await atomicWrite(this.configFile, `${json}\n`);
  }

  async rollbackSteps(steps, options = {}) {
    const state = await this.readHistory();
    if (state.stack.length === 0) {
      throw new Error('History is empty; nothing to rollback.');
    }

    let target = null;
    let consumed = 0;
    while (consumed < steps && state.stack.length > 0) {
      target = state.stack.pop();
      consumed += 1;
    }

    if (!target) {
      throw new Error('Not enough history entries for requested rollback.');
    }

    await this.applyModel(target);

    if (!options.keepHistory) {
      await this.writeHistory(state);
    }

    return target;
  }

  async pushHistory(modelId) {
    const state = await this.readHistory();
    state.stack.push(modelId);
    while (state.stack.length > MAX_STACK) {
      state.stack.shift();
    }
    await this.writeHistory(state);
  }

  async readHistory() {
    try {
      const content = await fs.readFile(this.historyFile, 'utf8');
      const parsed = JSON.parse(content);
      if (!Array.isArray(parsed.stack)) parsed.stack = [];
      if (typeof parsed.lastKnownGood !== 'string') parsed.lastKnownGood = null;
      return parsed;
    } catch {
      return { stack: [], lastKnownGood: null };
    }
  }

  async writeHistory(state) {
    await fs.mkdir(path.dirname(this.historyFile), { recursive: true });
    const text = JSON.stringify({
      stack: Array.isArray(state.stack) ? state.stack : [],
      lastKnownGood: state.lastKnownGood || null
    }, null, 2);
    await atomicWrite(this.historyFile, `${text}\n`);
  }

  async callGateway(method, params) {
    const response = await this.fetch(`${this.gatewayUrl.replace(/\/$/, '')}/rpc`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(this.gatewayToken ? { authorization: `Bearer ${this.gatewayToken}` } : {})
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: `${Date.now()}`,
        method,
        params
      })
    });

    if (!response.ok) {
      throw new Error(`Gateway HTTP ${response.status}`);
    }

    const payload = await response.json();
    if (payload.error) {
      throw new Error(payload.error.message || 'Gateway RPC error');
    }
    return payload.result;
  }
}

function safeError(error) {
  return String(error?.message || error || 'Unknown error').replace(/Bearer\s+[A-Za-z0-9._-]+/g, 'Bearer ***');
}

function parseCsvIds(value) {
  if (!value) return [];
  return String(value)
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);
}

async function atomicWrite(targetPath, content) {
  const tmpPath = `${targetPath}.tmp-${process.pid}-${Date.now()}`;
  await fs.writeFile(tmpPath, content, 'utf8');
  await fs.rename(tmpPath, targetPath);
}

function loadSupportedModels(customPath) {
  const file = customPath || path.join(__dirname, 'supported-models.json');
  const content = require(file);
  return content.models || [];
}

function providerFromModel(modelId) {
  return String(modelId || '').split('/')[0] || 'unknown';
}

module.exports = {
  DiscordModelSwitcher,
  atomicWrite,
  providerFromModel
};
