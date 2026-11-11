import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

async function render(metric) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', style: {}, addEventListener() {} });
    return elements.get(id);
  };
  const doc = { schema: 'client-observed-metrics@1', scope: 'client-observed', window_days: 7,
    environments: [], totals: { turns: { total: 2, completed: 2 }, outcomes: {}, models: [],
      environment_count: 1, client_ttft: metric, end_to_end: metric,
      segments: { tap_to_queued: metric } } };
  const window = {};
  window.parent = window;
  vm.runInNewContext(readFileSync(new URL('../single-repo/static/client-observed/app.js', import.meta.url), 'utf8'), {
    document: { getElementById: element, addEventListener() {} }, window,
    fetch: async () => ({ ok: true, json: async () => doc }),
  });
  await new Promise(resolve => setImmediate(resolve));
  return elements;
}

test('zero samples display unavailable rather than zero milliseconds', async () => {
  const elements = await render({ count: 0, avg_ms: 0, p95_ms: 0 });
  assert.match(elements.get('summary').innerHTML, /样本 0 · 覆盖 0\.0%/);
  assert.doesNotMatch(elements.get('summary').innerHTML, />0 ms</);
  assert.doesNotMatch(elements.get('segment-bars').innerHTML, />0 ms</);
});

test('an observed zero is rendered as a real measurement', async () => {
  const elements = await render({ count: 1, avg_ms: 0, p95_ms: 0 });
  assert.match(elements.get('summary').innerHTML, />0 ms</);
  assert.match(elements.get('summary').innerHTML, /样本 1 · 覆盖 50\.0%/);
});
