'use strict';

// All files and OAuth values are synthetic; fetch is injected in every test.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {refresh, queryParameters, SCOPES, TYPES} = require('../runtime/google_refresh.cjs');

const CLOCK = new Date('2026-11-14T18:00:00.123Z');
const CLIENT = {
  client_id: 'synthetic-client', client_secret: 'synthetic-secret',
  redirect_uris: ['http://localhost:3000/auth/callback'],
};

function response(value, status = 200, raw) {
  const bytes = Buffer.from(raw === undefined ? JSON.stringify(value) : raw);
  return {ok: status >= 200 && status < 300, status, redirected: false,
          arrayBuffer: async () => Uint8Array.from(bytes).buffer,
          body: {cancel: async () => {}}};
}

function dataType(url) {
  return new URL(url).pathname.split('/').slice(-2)[0];
}

async function fixture(callback, cacheOverrides = {}, client = CLIENT) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'synthetic-google-refresh-'));
  const tokenFile = path.join(root, '.google-health', 'tokens.json');
  const cached = {client_id: CLIENT.client_id, access_token: 'synthetic-access',
                  refresh_token: 'synthetic-refresh', scope: SCOPES.join(' '),
                  expires_at: CLOCK.getTime() / 1000 + 3600, ...cacheOverrides};
  try {
    await fs.mkdir(path.dirname(tokenFile));
    await fs.writeFile(tokenFile, JSON.stringify(cached));
    if (client) await fs.writeFile(path.join(root, 'client_secret_fixture.json'), JSON.stringify({web: client}));
    const options = {root, now: () => new Date(CLOCK), credentialDirectories: [root]};
    await callback({root, tokenFile, cached, options});
  } finally {
    await fs.rm(root, {recursive: true, force: true});
  }
}

async function runTests() {
  const passed = [];
  async function check(name, callback) {
    await callback();
    passed.push(name);
  }

  await check('query fields, source family, and civil boundary dates', async () => {
    const start = new Date('2026-11-01T00:00:00Z');
    const end = new Date('2026-11-08T00:00:00Z');
    const sleep = queryParameters('sleep', start, end);
    assert.equal(sleep.pageSize, 25);
    assert.equal(sleep.dataSourceFamily, undefined);
    assert.equal(sleep.filter, 'sleep.interval.end_time >= "2026-11-01T00:00:00Z" AND sleep.interval.end_time < "2026-11-08T00:00:00Z"');
    const hr = queryParameters('heart-rate', start, end);
    assert.equal(hr.pageSize, 10000);
    assert.equal(hr.dataSourceFamily, 'users/me/dataSourceFamilies/google-wearables');
    const daily = queryParameters('daily-resting-heart-rate', start, end, 'America/New_York');
    assert.equal(daily.filter, 'daily_resting_heart_rate.date >= "2026-10-31" AND daily_resting_heart_rate.date < "2026-11-08"');
    assert.equal(queryParameters('exercise', start, end, 'UTC').filter,
                 'exercise.interval.civil_start_time >= "2026-11-01" AND exercise.interval.civil_start_time < "2026-11-09"');
    assert.equal(queryParameters('irregular-rhythm-notification', start, end).filter, undefined);
    assert.equal(Object.keys(TYPES).length, 14);
  });

  await check('valid token exports 14 types privately without refreshing', () => fixture(async ({root, options}) => {
    const calls = [];
    const result = await refresh({...options, fetchImpl: async (url, request) => {
      calls.push({url, request});
      assert.equal(new URL(url).origin, 'https://health.googleapis.com');
      assert.equal(request.headers.Authorization, 'Bearer synthetic-access');
      assert.equal(request.redirect, 'error');
      return response({dataPoints: []});
    }});
    assert.equal(calls.length, 14);
    assert.equal(result.status, 'ok');
    assert.equal(result.records, 0);
    assert.equal(Object.keys(result.types).length, 14);
    assert.match(path.basename(result.destination), /^\d{8}T\d{12}Z$/);
    assert.equal(path.dirname(result.destination), path.join(root, 'api_data'));
    assert.equal((await fs.stat(result.destination)).mode & 0o777, 0o700);
    const summaryFile = path.join(result.destination, 'summary.json');
    assert.equal((await fs.stat(summaryFile)).mode & 0o777, 0o600);
    const summary = JSON.parse(await fs.readFile(summaryFile, 'utf8'));
    assert.equal(summary.started_at, '2026-11-14T18:00:00Z');
    assert.equal(summary.lookback_days, 2);
    assert.equal(Object.keys(summary.types).length, 14);
    assert(!JSON.stringify(result).includes('synthetic-access'));
    assert(!JSON.stringify(summary).includes('synthetic-secret'));
  }));

  await check('expired token refresh retains refresh grant and validates client match', () => fixture(async ({root, tokenFile, options}) => {
    await fs.writeFile(path.join(root, 'client_secret_other.json'), JSON.stringify({web: {...CLIENT, client_id: 'unrelated'}}));
    let tokenCalls = 0;
    await refresh({...options, fetchImpl: async (url, request) => {
      if (url === 'https://oauth2.googleapis.com/token') {
        tokenCalls += 1;
        const form = new URLSearchParams(request.body);
        assert.equal(form.get('client_id'), 'synthetic-client');
        assert.equal(form.get('refresh_token'), 'synthetic-refresh');
        assert.equal(form.get('grant_type'), 'refresh_token');
        assert.equal(request.headers.Authorization, undefined);
        return response({access_token: 'synthetic-new-access', expires_in: 3600});
      }
      assert.equal(request.headers.Authorization, 'Bearer synthetic-new-access');
      return response({dataPoints: []});
    }});
    assert.equal(tokenCalls, 1);
    const tokens = JSON.parse(await fs.readFile(tokenFile, 'utf8'));
    assert.equal(tokens.refresh_token, 'synthetic-refresh');
    assert.equal(tokens.scope, SCOPES.join(' '));
    assert.equal(tokens.expires_at, CLOCK.getTime() / 1000 + 3600);
    assert.equal((await fs.stat(tokenFile)).mode & 0o777, 0o600);
  }, {expires_at: 0}));

  await check('missing scopes fail before network and never begin consent', () => fixture(async ({root, options}) => {
    let calls = 0;
    await assert.rejects(refresh({...options, fetchImpl: async () => {calls += 1;}}), {code: 'OAUTH_SCOPES_MISSING'});
    assert.equal(calls, 0);
    await assert.rejects(fs.access(path.join(root, 'api_data')), {code: 'ENOENT'});
  }, {scope: SCOPES[0]}));

  await check('wrong OAuth client cannot use cached credentials', () => fixture(async ({options}) => {
    let calls = 0;
    await assert.rejects(refresh({...options, fetchImpl: async () => {calls += 1;}}), {code: 'MATCHING_OAUTH_CLIENT_NOT_FOUND'});
    assert.equal(calls, 0);
  }, {}, {...CLIENT, client_id: 'different-client'}));

  await check('expired cache without refresh token fails without consent', () => fixture(async ({options}) => {
    let calls = 0;
    await assert.rejects(refresh({...options, fetchImpl: async () => {calls += 1;}}), {code: 'OAUTH_REFRESH_TOKEN_REQUIRED'});
    assert.equal(calls, 0);
  }, {expires_at: 0, refresh_token: undefined}));

  await check('token HTTP failures never expose response body', () => fixture(async ({root, options}) => {
    const sensitiveBody = 'synthetic-private-token-error-body';
    await assert.rejects(refresh({...options, fetchImpl: async () => response({error_description: sensitiveBody}, 400)}), error => {
      assert.equal(error.code, 'HTTP_ERROR');
      assert.equal(error.status, 400);
      assert(!error.message.includes(sensitiveBody));
      return true;
    });
    await assert.rejects(fs.access(path.join(root, 'api_data')), {code: 'ENOENT'});
  }, {expires_at: 0}));

  await check('refreshed scope loss never overwrites valid cache', () => fixture(async ({tokenFile, cached, options}) => {
    await assert.rejects(refresh({...options, fetchImpl: async () => response({access_token: 'synthetic-new', scope: SCOPES[0]})}),
                         {code: 'OAUTH_SCOPES_MISSING'});
    assert.deepEqual(JSON.parse(await fs.readFile(tokenFile, 'utf8')), cached);
  }, {expires_at: 0}));

  await check('pagination encodes opaque tokens and preserves original pages', () => fixture(async ({options}) => {
    const original = ' { "dataPoints" : [{"fixture":1}], "nextPageToken": "a+b/c=" }\n';
    let heartCalls = 0;
    const result = await refresh({...options, fetchImpl: async url => {
      if (dataType(url) !== 'heart-rate') return response({dataPoints: []});
      heartCalls += 1;
      if (heartCalls === 1) return response(null, 200, original);
      assert.equal(new URL(url).searchParams.get('pageToken'), 'a+b/c=');
      return response({dataPoints: [{fixture: 2}]});
    }});
    assert.equal(result.types['heart-rate'].records, 2);
    assert.equal(result.types['heart-rate'].pages, 2);
    assert.equal(await fs.readFile(path.join(result.destination, 'heart-rate', 'page-00001.json'), 'utf8'), original);
  }));

  await check('repeated page token stops with partial pages marked', () => fixture(async ({options}) => {
    let calls = 0;
    const result = await refresh({...options, fetchImpl: async url => {
      if (dataType(url) !== 'steps') return response({dataPoints: []});
      calls += 1;
      return response({dataPoints: [], nextPageToken: 'same'});
    }});
    assert.equal(calls, 2);
    assert.equal(result.status, 'partial');
    assert.equal(result.types.steps.error_type, 'REPEATED_PAGE_TOKEN');
    assert.equal((await fs.readdir(path.join(result.destination, 'steps'))).length, 2);
    const summary = JSON.parse(await fs.readFile(path.join(result.destination, 'summary.json'), 'utf8'));
    assert.equal(summary.types.steps.note, 'Any saved pages are partial.');
  }));

  await check('page safety limit stops unique infinite tokens', () => fixture(async ({options}) => {
    let calls = 0;
    const result = await refresh({...options, maxPages: 2, fetchImpl: async url => {
      if (dataType(url) !== 'steps') return response({dataPoints: []});
      calls += 1;
      return response({dataPoints: [], nextPageToken: `next-${calls}`});
    }});
    assert.equal(calls, 2);
    assert.equal(result.types.steps.error_type, 'PAGINATION_LIMIT_REACHED');
  }));

  await check('HTTP errors and thrown transport errors are sanitized', () => fixture(async ({options}) => {
    const sentinel = 'synthetic-secret-never-log';
    const result = await refresh({...options, fetchImpl: async url => {
      if (dataType(url) === 'steps') return response({error: {message: sentinel}}, 403);
      if (dataType(url) === 'distance') throw new Error(`network details ${sentinel}`);
      return response({dataPoints: []});
    }});
    assert.equal(result.error_count, 2);
    assert.equal(result.types.steps.http_status, 403);
    assert.equal(result.types.distance.error_type, 'REQUEST_FAILED');
    const summary = await fs.readFile(path.join(result.destination, 'summary.json'), 'utf8');
    assert(!summary.includes(sentinel));
    assert(!JSON.stringify(result).includes(sentinel));
  }));

  await check('timeout bounds a stalled fetch and continues other types', () => fixture(async ({options}) => {
    const result = await refresh({...options, timeoutMs: 5, fetchImpl: async url => {
      if (dataType(url) === 'steps') return new Promise(() => {});
      return response({dataPoints: []});
    }});
    assert.equal(result.types.steps.error_type, 'REQUEST_TIMEOUT');
    assert.equal(result.types.distance.status, 'empty');
  }));

  await check('TCX strict resource names, duplicate handling, and original bytes', () => fixture(async ({options}) => {
    const valid = {name: 'users/me/dataTypes/exercise/dataPoints/workout_1'};
    const invalid = {name: 'https://example.invalid/steal-token'};
    const tcx = '<?xml version="1.0"?>\n<TrainingCenterDatabase/>\n';
    const tcxCalls = [];
    const result = await refresh({...options, fetchImpl: async (url, request) => {
      assert(['health.googleapis.com', 'oauth2.googleapis.com'].includes(new URL(url).hostname));
      if (url.includes(':exportExerciseTcx')) {
        tcxCalls.push(url);
        assert.equal(request.headers.Accept, 'application/xml');
        assert.equal(new URL(url).searchParams.get('partialData'), 'true');
        return response(null, 200, tcx);
      }
      return response({dataPoints: dataType(url) === 'exercise' ? [valid, valid, invalid] : []});
    }});
    assert.equal(tcxCalls.length, 1);
    assert.equal(result.exercise_tcx.files, 1);
    assert.equal(result.exercise_tcx.status, 'partial');
    assert.equal(await fs.readFile(path.join(result.destination, 'exercise-tcx', 'workout_1.tcx'), 'utf8'), tcx);
  }));

  await check('same-time exports never overwrite an existing timestamp folder', () => fixture(async ({options}) => {
    const fetchImpl = async () => response({dataPoints: []});
    const one = await refresh({...options, fetchImpl});
    const two = await refresh({...options, fetchImpl});
    assert.notEqual(one.destination, two.destination);
    assert.equal(Object.keys(JSON.parse(await fs.readFile(path.join(one.destination, 'summary.json'), 'utf8')).types).length, 14);
  }));

  return {passed: passed.length, tests: passed};
}

module.exports = {runTests};

if (require.main === module) {
  runTests().then(result => console.log(JSON.stringify(result)), error => {
    console.error(error);
    process.exitCode = 1;
  });
}
