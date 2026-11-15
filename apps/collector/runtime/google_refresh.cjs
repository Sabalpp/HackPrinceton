'use strict';

// Headless counterpart to google_health.py. Requiring this module performs no I/O.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');

const REDIRECT = 'http://localhost:3000/auth/callback';
const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const API_URL = 'https://health.googleapis.com/v4/users/me/dataTypes';
const SOURCE_FAMILY = 'users/me/dataSourceFamilies/google-wearables';
const SCOPES = Object.freeze([
  'activity_and_fitness', 'health_metrics_and_measurements', 'sleep', 'irn', 'location',
].map(scope => `https://www.googleapis.com/auth/googlehealth.${scope}.readonly`));
const TYPES = Object.freeze({
  'heart-rate': 'sample_time.physical_time',
  'daily-resting-heart-rate': 'date',
  'heart-rate-variability': 'sample_time.physical_time',
  'daily-heart-rate-variability': 'date',
  'oxygen-saturation': 'sample_time.physical_time',
  'daily-oxygen-saturation': 'date',
  'daily-respiratory-rate': 'date',
  'daily-sleep-temperature-derivations': 'date',
  sleep: 'interval.end_time',
  steps: 'interval.start_time',
  distance: 'interval.start_time',
  'active-zone-minutes': 'interval.start_time',
  exercise: 'interval.civil_start_time',
  'irregular-rhythm-notification': null,
});
const EXERCISE_NAME = /^users\/(?:me|[0-9]+)\/dataTypes\/exercise\/dataPoints\/[A-Za-z0-9_-]+$/;

class RefreshError extends Error {
  constructor(code, status) {
    super(status === undefined ? code : `${code}: HTTP ${status}`);
    this.name = 'RefreshError';
    this.code = code;
    if (status !== undefined) this.status = status;
  }
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function text(value) {
  return typeof value === 'string' && value.length > 0;
}

function stamp(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) throw new RefreshError('INVALID_TIME');
  return date.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function localDate(value, timezone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(value);
  const found = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${found.year}-${found.month}-${found.day}`;
}

function nextDate(value) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

function queryParameters(dataType, start, end, timezone = 'America/New_York') {
  if (!Object.prototype.hasOwnProperty.call(TYPES, dataType)) throw new RefreshError('UNKNOWN_DATA_TYPE');
  const field = TYPES[dataType];
  if (field === null) return {pageSize: 25, dataSourceFamily: SOURCE_FAMILY};
  const civil = field === 'date' || field === 'interval.civil_start_time';
  const lower = civil ? localDate(start, timezone) : stamp(start);
  const upper = civil ? nextDate(localDate(end, timezone)) : stamp(end);
  const key = `${dataType.replace(/-/g, '_')}.${field}`;
  const result = {
    pageSize: dataType === 'sleep' || dataType === 'exercise' ? 25 : 10000,
    filter: `${key} >= "${lower}" AND ${key} < "${upper}"`,
  };
  if (dataType !== 'sleep') result.dataSourceFamily = SOURCE_FAMILY;
  return result;
}

async function privateBytes(filename, bytes) {
  const parent = path.dirname(filename);
  await fs.mkdir(parent, {recursive: true, mode: 0o700});
  await fs.chmod(parent, 0o700);
  const temporary = `${filename}.${crypto.randomUUID()}.tmp`;
  let handle;
  try {
    handle = await fs.open(temporary, 'wx', 0o600);
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close();
    handle = null;
    await fs.rename(temporary, filename);
  } catch (_) {
    throw new RefreshError('PRIVATE_FILE_WRITE_FAILED');
  } finally {
    if (handle) await handle.close().catch(() => {});
    await fs.unlink(temporary).catch(() => {});
  }
}

async function privateJSON(filename, value) {
  return privateBytes(filename, Buffer.from(`${JSON.stringify(value, null, 2)}\n`));
}

async function readJSON(filename, code) {
  try {
    const value = JSON.parse(await fs.readFile(filename, 'utf8'));
    if (!object(value)) throw new Error();
    return value;
  } catch (_) {
    throw new RefreshError(code);
  }
}

function hasScopes(tokens) {
  const granted = new Set(typeof tokens.scope === 'string' ? tokens.scope.split(/\s+/) : []);
  return SCOPES.every(scope => granted.has(scope));
}

async function matchingCredentials(clientId, directories) {
  const matches = new Map();
  for (const directory of new Set(directories.map(value => path.resolve(value)))) {
    let names;
    try {
      names = await fs.readdir(directory);
    } catch (_) {
      continue;
    }
    for (const name of names.sort()) {
      if (!/^client_secret.*\.json$/.test(name)) continue;
      let document;
      try {
        document = JSON.parse(await fs.readFile(path.join(directory, name), 'utf8'));
      } catch (_) {
        continue;
      }
      const config = document && document.web;
      if (!object(config) || config.client_id !== clientId || !text(config.client_secret) ||
          !Array.isArray(config.redirect_uris) || !config.redirect_uris.includes(REDIRECT)) continue;
      // Duplicate copies of the same downloaded client are harmless.
      matches.set(config.client_secret, config);
    }
  }
  if (matches.size === 0) throw new RefreshError('MATCHING_OAUTH_CLIENT_NOT_FOUND');
  if (matches.size !== 1) throw new RefreshError('AMBIGUOUS_OAUTH_CLIENT');
  return matches.values().next().value;
}

function allowedEndpoint(address, form) {
  const url = new URL(address);
  if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash) return false;
  if (form !== undefined) return url.origin === 'https://oauth2.googleapis.com' && url.pathname === '/token' && !url.search;
  return url.origin === 'https://health.googleapis.com' && url.pathname.startsWith('/v4/');
}

async function requestBytes(address, {token, form, accept = 'application/json', fetchImpl, timeoutMs}) {
  if (!allowedEndpoint(address, form)) throw new RefreshError('DISALLOWED_GOOGLE_ENDPOINT');
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new RefreshError('REQUEST_TIMEOUT'));
    }, timeoutMs);
  });
  const operation = (async () => {
    const headers = {Accept: accept};
    if (token) headers.Authorization = `Bearer ${token}`;
    if (form !== undefined) headers['Content-Type'] = 'application/x-www-form-urlencoded';
    let response;
    try {
      response = await fetchImpl(address, {
        method: form === undefined ? 'GET' : 'POST', headers,
        body: form === undefined ? undefined : new URLSearchParams(form).toString(),
        redirect: 'error', signal: controller.signal,
      });
    } catch (_) {
      throw new RefreshError(controller.signal.aborted ? 'REQUEST_TIMEOUT' : 'REQUEST_FAILED');
    }
    if (response.redirected) throw new RefreshError('UNEXPECTED_REDIRECT');
    if (!response.ok) {
      if (response.body && typeof response.body.cancel === 'function') await response.body.cancel().catch(() => {});
      throw new RefreshError('HTTP_ERROR', response.status);
    }
    try {
      return Buffer.from(await response.arrayBuffer());
    } catch (_) {
      throw new RefreshError(controller.signal.aborted ? 'REQUEST_TIMEOUT' : 'RESPONSE_READ_FAILED');
    }
  })();
  try {
    return await Promise.race([operation, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

async function requestJSON(address, options) {
  const bytes = await requestBytes(address, options);
  try {
    const value = JSON.parse(bytes.toString('utf8'));
    if (!object(value)) throw new Error();
    return {value, bytes};
  } catch (_) {
    throw new RefreshError('INVALID_JSON_RESPONSE');
  }
}

async function accessToken(root, options) {
  const filename = path.join(root, '.google-health', 'tokens.json');
  const cached = await readJSON(filename, 'OAUTH_CACHE_REQUIRED');
  if (!text(cached.client_id)) throw new RefreshError('CACHED_CLIENT_ID_MISSING');
  if (!hasScopes(cached)) throw new RefreshError('OAUTH_SCOPES_MISSING');
  const config = await matchingCredentials(cached.client_id, options.credentialDirectories);
  const currentSeconds = options.now().getTime() / 1000;
  if (text(cached.access_token) && Number(cached.expires_at) > currentSeconds + 120) return cached.access_token;
  if (!text(cached.refresh_token)) throw new RefreshError('OAUTH_REFRESH_TOKEN_REQUIRED');
  const {value: fresh} = await requestJSON(TOKEN_URL, {
    ...options,
    form: {client_id: config.client_id, client_secret: config.client_secret,
           refresh_token: cached.refresh_token, grant_type: 'refresh_token'},
  });
  if (!text(fresh.access_token)) throw new RefreshError('TOKEN_RESPONSE_MISSING_ACCESS_TOKEN');
  const lifetime = Number(fresh.expires_in === undefined ? 3600 : fresh.expires_in);
  if (!Number.isFinite(lifetime) || lifetime <= 0) throw new RefreshError('INVALID_TOKEN_LIFETIME');
  const tokens = {...cached, ...fresh, client_id: config.client_id,
                  expires_at: options.now().getTime() / 1000 + lifetime};
  if (!hasScopes(tokens)) throw new RefreshError('OAUTH_SCOPES_MISSING');
  await privateJSON(filename, tokens);
  return tokens.access_token;
}

async function createDestination(root, end) {
  const parent = path.join(root, 'api_data');
  await fs.mkdir(parent, {recursive: true, mode: 0o700});
  await fs.chmod(parent, 0o700);
  const prefix = end.toISOString().slice(0, 19).replace(/[-:]/g, '');
  for (let attempt = 0; attempt < 1000; attempt += 1) {
    const microseconds = String(end.getUTCMilliseconds() * 1000 + attempt).padStart(6, '0');
    const destination = path.join(parent, `${prefix}${microseconds}Z`);
    try {
      await fs.mkdir(destination, {mode: 0o700});
      return destination;
    } catch (error) {
      if (error.code !== 'EEXIST') throw new RefreshError('EXPORT_DIRECTORY_FAILED');
    }
  }
  throw new RefreshError('EXPORT_DIRECTORY_COLLISION');
}

function errorEntry(error) {
  return {error: error instanceof RefreshError ? error.message : 'EXPORT_FAILED',
          error_type: error instanceof RefreshError ? error.code : 'EXPORT_FAILED',
          ...(error instanceof RefreshError && error.status !== undefined ? {http_status: error.status} : {})};
}

async function fetchType(dataType, token, start, end, destination, options) {
  const query = queryParameters(dataType, start, end, options.timezone);
  const params = {...query};
  const seen = new Set();
  let count = 0;
  for (let page = 1; page <= options.maxPages; page += 1) {
    const url = `${API_URL}/${dataType}/dataPoints?${new URLSearchParams(params)}`;
    const {value: response, bytes} = await requestJSON(url, {...options, token});
    if (response.dataPoints !== undefined && !Array.isArray(response.dataPoints)) throw new RefreshError('INVALID_DATA_POINTS');
    const filename = path.join(destination, dataType, `page-${String(page).padStart(5, '0')}.json`);
    await privateBytes(filename, bytes);
    count += (response.dataPoints || []).length;
    const next = response.nextPageToken;
    if (!next) return {status: count ? 'ok' : 'empty', records: count, pages: page, query};
    if (!text(next)) throw new RefreshError('INVALID_PAGE_TOKEN');
    if (seen.has(next)) throw new RefreshError('REPEATED_PAGE_TOKEN');
    seen.add(next);
    params.pageToken = next;
  }
  throw new RefreshError('PAGINATION_LIMIT_REACHED');
}

async function exportExercises(token, destination, options) {
  const failures = [];
  const seen = new Set();
  let exported = 0;
  const exerciseDirectory = path.join(destination, 'exercise');
  let files = [];
  try {
    files = (await fs.readdir(exerciseDirectory)).filter(name => /^page-\d+\.json$/.test(name)).sort();
  } catch (error) {
    if (error.code !== 'ENOENT') throw new RefreshError('EXERCISE_PAGE_READ_FAILED');
  }
  for (const filename of files) {
    const body = await readJSON(path.join(exerciseDirectory, filename), 'EXERCISE_PAGE_READ_FAILED');
    for (const point of body.dataPoints || []) {
      const name = object(point) && text(point.name) ? point.name : '';
      if (seen.has(name)) continue;
      seen.add(name);
      if (!EXERCISE_NAME.test(name)) {
        failures.push(errorEntry(new RefreshError('INVALID_EXERCISE_RESOURCE_NAME')));
        continue;
      }
      const identifier = name.split('/').pop();
      const url = `https://health.googleapis.com/v4/${name}:exportExerciseTcx?alt=media&partialData=true`;
      try {
        const bytes = await requestBytes(url, {...options, token, accept: 'application/xml'});
        if (!bytes.toString('utf8').trim()) throw new RefreshError('EMPTY_TCX_RESPONSE');
        await privateBytes(path.join(destination, 'exercise-tcx', `${identifier}.tcx`), bytes);
        exported += 1;
      } catch (error) {
        failures.push(errorEntry(error));
      }
    }
  }
  return {status: failures.length ? 'partial' : exported ? 'ok' : 'empty',
          files: exported, errors: failures,
          note: 'TCX may contain HR/activity without GPS. No route is invented when GPS is absent.'};
}

/**
 * Export using existing consent only. Tests may inject fetchImpl, now, and
 * credentialDirectories pointing exclusively to temporary synthetic fixtures.
 * No OAuth tokens, client identifiers, or response bodies are returned or logged.
 */
async function refresh({root, days = 2, timezone = 'America/New_York',
                        fetchImpl = globalThis.fetch, now = () => new Date(),
                        credentialDirectories, timeoutMs = 45000, maxPages = 1000} = {}) {
  if (!text(root)) throw new RefreshError('PROJECT_ROOT_REQUIRED');
  if (!Number.isInteger(days) || days < 1 || days > 90) throw new RefreshError('INVALID_LOOKBACK_DAYS');
  if (typeof fetchImpl !== 'function') throw new RefreshError('FETCH_UNAVAILABLE');
  if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 1000) throw new RefreshError('INVALID_PAGE_LIMIT');
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 45000) throw new RefreshError('INVALID_REQUEST_TIMEOUT');
  const end = now();
  if (!(end instanceof Date) || !Number.isFinite(end.getTime())) throw new RefreshError('INVALID_TIME');
  try {
    localDate(end, timezone);
  } catch (_) {
    throw new RefreshError('INVALID_TIMEZONE');
  }
  root = path.resolve(root);
  const options = {fetchImpl, now, timezone, timeoutMs, maxPages,
                   credentialDirectories: credentialDirectories || [root, path.join(os.homedir(), 'Downloads')]};
  const token = await accessToken(root, options);
  const destination = await createDestination(root, end);
  const start = new Date(end.getTime() - days * 86400000);
  const report = {
    started_at: stamp(end), lookback_days: days,
    note: 'Synced API records, not live BLE or unprocessed optical sensor samples. ' +
          'Sleep includes all sources; other types are filtered to Google/Fitbit wearables. ' +
          'Daily and exercise queries include complete boundary calendar dates. ' +
          'Irregular rhythm notifications include all available alert history without a date filter.',
    types: {},
  };
  let errors = 0;
  for (const dataType of Object.keys(TYPES)) {
    try {
      report.types[dataType] = await fetchType(dataType, token, start, end, destination, options);
    } catch (error) {
      errors += 1;
      report.types[dataType] = {status: 'error', ...errorEntry(error), note: 'Any saved pages are partial.'};
    }
    await privateJSON(path.join(destination, 'summary.json'), report);
  }
  try {
    report.exercise_tcx = await exportExercises(token, destination, options);
  } catch (error) {
    report.exercise_tcx = {status: 'partial', files: 0, errors: [errorEntry(error)]};
  }
  errors += report.exercise_tcx.errors.length;
  await privateJSON(path.join(destination, 'summary.json'), report);
  const types = Object.fromEntries(Object.entries(report.types).map(([type, entry]) => [type, {
    status: entry.status, records: entry.records || 0, pages: entry.pages || 0,
    ...(entry.error_type ? {error_type: entry.error_type} : {}),
    ...(entry.http_status === undefined ? {} : {http_status: entry.http_status}),
  }]));
  return {status: errors ? 'partial' : 'ok', destination, error_count: errors,
          records: Object.values(types).reduce((sum, entry) => sum + entry.records, 0),
          types, exercise_tcx: {status: report.exercise_tcx.status, files: report.exercise_tcx.files}};
}

module.exports = {refresh, queryParameters, SCOPES, TYPES, RefreshError};
