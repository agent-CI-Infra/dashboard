#!/usr/bin/env node
/**
 * Merge one DigitalMate `digitalmate/e2e-performance@1` run into the
 * `digitalmate/e2e-performance-history@1` file published by the dashboard.
 *
 * History keeps one point per case per Asia/Shanghai calendar day. A rerun on
 * the same day replaces the point only when it finished later; `latest` always
 * keeps the newest case payload (including steps) for the detail view.
 */
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const PERFORMANCE_SCHEMA = 'digitalmate/e2e-performance@1';
const HISTORY_SCHEMA = 'digitalmate/e2e-performance-history@1';
const MAX_POINTS = 180;
const TIME_ZONE = 'Asia/Shanghai';

function parseArgs(argv) {
  const out = { run: null, history: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--run') out.run = argv[++i];
    else if (argv[i] === '--history') out.history = argv[++i];
    else if (argv[i] === '--help' || argv[i] === '-h') out.help = true;
    else throw new Error(`unknown argument: ${argv[i]}`);
  }
  return out;
}

function timestamp(value) {
  const ms = Date.parse(value || '');
  return Number.isFinite(ms) ? ms : -Infinity;
}

function dateInBeijing(value) {
  const ms = Date.parse(value || '');
  if (!Number.isFinite(ms)) return null;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(ms));
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function publicDevice(device) {
  if (!device || typeof device !== 'object') return null;
  const screen = device.screen && typeof device.screen === 'object'
    ? {
        width: device.screen.width ?? null,
        height: device.screen.height ?? null,
        measured: Boolean(device.screen.measured),
      }
    : null;
  return { model: device.model ?? null, screen };
}

function readJson(file) {
  return JSON.parse(readFileSync(file, 'utf8'));
}

function writeJson(file, value) {
  mkdirSync(dirname(resolve(file)), { recursive: true });
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

export function mergeHistory(history, performance) {
  if (!performance || !Array.isArray(performance.cases)) {
    throw new Error('performance object has no cases array');
  }
  const target = history || {
    schema: HISTORY_SCHEMA,
    repository: performance.repository || 'openJiuwen-ai/DigitalMate',
    updated_at: null,
    cases: {},
  };
  target.schema = HISTORY_SCHEMA;
  target.repository = performance.repository || target.repository;
  target.cases = target.cases || {};

  for (const item of performance.cases) {
    if (!item || typeof item.id !== 'string' || !item.id) continue;
    const entry = (target.cases[item.id] ||= {
      id: item.id,
      title: item.title || item.id,
      mode: item.mode ?? null,
      points: [],
      latest: null,
    });
    entry.title = item.title || entry.title;
    entry.mode = item.mode ?? entry.mode;
    entry.points = Array.isArray(entry.points) ? entry.points : [];

    const date = dateInBeijing(item.finished_at || item.started_at || performance.generated_at);
    if (date) {
      const point = {
        date,
        status: item.status,
        query_ms: item.query_ms ?? null,
        query_count: item.query_count ?? 0,
        duration_ms: item.duration_ms ?? null,
        run_id: performance.run_id,
        commit: performance.commit ?? null,
        finished_at: item.finished_at ?? null,
      };
      const existing = entry.points.find((candidate) => candidate.date === date);
      if (!existing) entry.points.push(point);
      else if (timestamp(item.finished_at) >= timestamp(existing.finished_at)) Object.assign(existing, point);
      entry.points.sort((a, b) => String(a.date).localeCompare(String(b.date)));
      entry.points = entry.points.slice(-MAX_POINTS);
    }

    const latestAt = timestamp(entry.latest?.finished_at);
    if (!entry.latest || timestamp(item.finished_at) >= latestAt) {
      entry.latest = {
        ...item,
        device: publicDevice(item.device),
        run_id: performance.run_id,
        commit: performance.commit ?? null,
        generated_at: performance.generated_at,
      };
    }
  }

  target.updated_at = performance.generated_at || target.updated_at;
  return target;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.run || !args.history) {
    console.error('usage: merge-e2e-performance.mjs --run <e2e-performance.json> --history <history.json>');
    process.exit(args.help ? 0 : 2);
  }
  const performance = readJson(args.run);
  if (performance.schema && performance.schema !== PERFORMANCE_SCHEMA) {
    throw new Error(`unexpected performance schema: ${performance.schema}`);
  }
  // Reject pre-query-metric artifacts: merging them would overwrite a good
  // same-day point with query_ms=null and silently regress the dashboard.
  const stale = (performance.cases || []).filter(
    (item) => item && typeof item === 'object'
      && !Object.prototype.hasOwnProperty.call(item, 'query_ms'),
  );
  if (stale.length) {
    throw new Error(
      `artifact ${performance.run_id || '(unknown)'} predates the query-latency metric `
      + `(${stale.length} case(s) missing query_ms); rerun the collector/nightly`,
    );
  }
  let history = null;
  try {
    history = readJson(args.history);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  const merged = mergeHistory(history, performance);
  writeJson(args.history, merged);
  console.log(`merged ${performance.cases.length} case(s) from ${performance.run_id} into ${args.history}`);
}

if (process.argv[1] && realpathSync(resolve(process.argv[1])) === realpathSync(fileURLToPath(import.meta.url))) main();
