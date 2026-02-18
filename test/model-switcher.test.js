const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const { DiscordModelSwitcher } = require('../model-switcher');

function makeGatewayFetch(state) {
  return async (_url, options) => {
    const body = JSON.parse(options.body);

    if (body.method === 'config.get') {
      return ok({
        agents: {
          defaults: { model: { primary: state.current, fallbacks: ['openrouter/deepseek/deepseek-v3.2'] } },
          list: [{ id: 'main', model: { primary: state.current } }]
        }
      });
    }

    if (body.method === 'config.patch') {
      const next = body.params.patch.agents.defaults.model.primary;
      if (state.failFor.has(next)) {
        return okError('Unknown model');
      }
      state.current = next;
      return ok({ ok: true });
    }

    return http(404, {});
  };
}

function ok(result) {
  return {
    ok: true,
    status: 200,
    async json() {
      return { jsonrpc: '2.0', result };
    }
  };
}

function okError(message) {
  return {
    ok: true,
    status: 200,
    async json() {
      return { jsonrpc: '2.0', error: { message } };
    }
  };
}

function http(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload;
    }
  };
}

test('pushHistory keeps max length 10', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'rex-model-'));
  const historyFile = path.join(tmp, 'history.json');

  const state = { current: 'openrouter/anthropic/claude-sonnet-4.6', failFor: new Set() };
  const switcher = new DiscordModelSwitcher({
    historyFile,
    allowedModels: [
      'openrouter/anthropic/claude-sonnet-4.6',
      'openrouter/anthropic/claude-opus-4.6'
    ],
    adminUserIds: '1',
    fetchImpl: makeGatewayFetch(state)
  });

  for (let i = 0; i < 12; i += 1) {
    await switcher.pushHistory(`openrouter/anthropic/claude-sonnet-4.${i}`);
  }

  const history = await switcher.readHistory();
  assert.equal(history.stack.length, 10);
  assert.equal(history.stack[0], 'openrouter/anthropic/claude-sonnet-4.2');
});

test('rollback pops requested steps and applies target', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'rex-model-'));
  const historyFile = path.join(tmp, 'history.json');

  const state = { current: 'openrouter/anthropic/claude-opus-4.6', failFor: new Set() };
  const switcher = new DiscordModelSwitcher({
    historyFile,
    allowedModels: [
      'openrouter/anthropic/claude-sonnet-4.6',
      'openrouter/anthropic/claude-opus-4.6',
      'openrouter/deepseek/deepseek-v3.2'
    ],
    adminUserIds: '1',
    fetchImpl: makeGatewayFetch(state)
  });

  await switcher.writeHistory({
    stack: [
      'openrouter/anthropic/claude-sonnet-4.6',
      'openrouter/deepseek/deepseek-v3.2',
      'openrouter/anthropic/claude-opus-4.6'
    ],
    lastKnownGood: null
  });

  const target = await switcher.rollbackSteps(2);
  assert.equal(target, 'openrouter/deepseek/deepseek-v3.2');
  assert.equal(state.current, 'openrouter/deepseek/deepseek-v3.2');

  const remaining = await switcher.readHistory();
  assert.deepEqual(remaining.stack, ['openrouter/anthropic/claude-sonnet-4.6']);
});

test('set auto-rolls back on apply error', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'rex-model-'));
  const historyFile = path.join(tmp, 'history.json');

  const state = {
    current: 'openrouter/anthropic/claude-sonnet-4.6',
    failFor: new Set(['openrouter/anthropic/claude-opus-4.6'])
  };

  const switcher = new DiscordModelSwitcher({
    historyFile,
    allowedModels: [
      'openrouter/anthropic/claude-sonnet-4.6',
      'openrouter/anthropic/claude-opus-4.6'
    ],
    adminUserIds: '1',
    fetchImpl: makeGatewayFetch(state)
  });

  const replies = [];
  await switcher.handleDiscordMessage({
    content: '/model set openrouter/anthropic/claude-opus-4.6',
    author: { id: '1' },
    member: { roles: [] },
    reply: async (msg) => replies.push(msg)
  });

  assert.equal(state.current, 'openrouter/anthropic/claude-sonnet-4.6');
  assert.equal(replies.length, 1);
  assert.match(replies[0], /Rolled back/);
});
