import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {validateSettings, wranglerConfig, apiEnvironment} from '../settings.mjs';
import {containerClass, handlers} from '../src/platform.mjs';

const defaults = parseEnv(readFileSync(new URL('../.env.example', import.meta.url), 'utf8'));
const settings = {...defaults, CLOUDFLARE_ACCOUNT_ID: 'a'.repeat(32),
  PUBLIC_ORIGIN: 'https://farcare.synthetic.workers.dev',
  DATABASE_URL: 'postgresql://fixture:fixture@db.invalid/farcare?sslmode=require',
  INGEST_API_KEY: 'synthetic-'.repeat(5)};

test('generated configuration never contains database, provider, or Cloudflare credentials', () => {
  const s = {...settings, CLOUDFLARE_API_TOKEN: 'private-cf-value', PHOTON_API_KEY: 'private-photon-value', GEMINI_API_KEY: 'private-gemini-value'};
  assert.deepEqual(validateSettings(s), []);
  const config = wranglerConfig(s, 'synthetic-revision');
  const text = JSON.stringify(config);
  for (const secret of [s.DATABASE_URL, s.INGEST_API_KEY, s.CLOUDFLARE_API_TOKEN, s.PHOTON_API_KEY, s.GEMINI_API_KEY]) assert.ok(!text.includes(secret));
  assert.equal(config.vars.GIT_COMMIT, 'synthetic-revision');
  assert.equal(config.containers.length, 2);
  assert.deepEqual(config.triggers.crons, ['* * * * *']);
  assert.equal(config.preview_urls, false);
  assert.deepEqual(config.secrets.required, ['DATABASE_URL', 'INGEST_API_KEY']);
});

test('custom domain disables alternate workers.dev origin', () => {
  const s = {...settings, CUSTOM_DOMAIN: 'true', PUBLIC_ORIGIN: 'https://care.synthetic.test'};
  assert.deepEqual(validateSettings(s), []);
  const config = wranglerConfig(s, 'test');
  assert.equal(config.workers_dev, false);
  assert.deepEqual(config.routes, [{pattern: 'care.synthetic.test', custom_domain: true}]);
});

test('production rejects unsafe origins, plaintext database transport, bad sizes and unidentified sensor mapping', () => {
  for (const change of [
    {PUBLIC_ORIGIN: 'http://farcare.synthetic.workers.dev'},
    {PUBLIC_ORIGIN: 'https://other.synthetic.workers.dev'},
    {PUBLIC_ORIGIN: 'https://farcare.synthetic.workers.dev/path'},
    {PUBLIC_ORIGIN: 'https://hackprinceton.pages.dev', CUSTOM_DOMAIN: 'true'},
    {DATABASE_URL: 'sqlite:///local.db'},
    {DATABASE_URL: 'postgresql://fixture:fixture@db.invalid/farcare'},
    {API_INSTANCE_TYPE: 'unbounded'}, {SESSION_HOURS: 'NaN'},
    {INGEST_API_KEY: 'short'}, {TIGER_SYNC_ENABLED: 'true', TIGER_PARENT_ID: ''},
  ]) assert.ok(validateSettings({...settings, ...change}).length > 0, JSON.stringify(Object.keys(change)));
});

test('Photon cannot silently fall back, sleep through care work, or accept unsigned webhooks', () => {
  assert.ok(validateSettings({...settings, MESSAGING_CHANNEL: 'photon'}).some(x => x.includes('PHOTON_WEBHOOK_SECRET')));
  const live = {...settings, MESSAGING_CHANNEL: 'photon', PHOTON_API_KEY: 'synthetic', PHOTON_PROJECT_ID: 'synthetic', PHOTON_WEBHOOK_SECRET: 'synthetic'};
  assert.deepEqual(validateSettings(live), []);
  assert.deepEqual(wranglerConfig(live, 'test').secrets.required, [
    'DATABASE_URL', 'INGEST_API_KEY', 'PHOTON_API_KEY', 'PHOTON_PROJECT_ID', 'PHOTON_WEBHOOK_SECRET',
  ]);
  assert.ok(validateSettings({...live, CARE_AGENT_MODE: 'on_demand'}).length);
  assert.equal(apiEnvironment(live).PHOTON_DELIVERY, 'webhook');
  assert.equal(apiEnvironment({...live, MESSAGING_CHANNEL: 'console'}).PHOTON_DELIVERY, 'off');
});

test('container environment enforces production guards without inheriting operator credentials', () => {
  const env = apiEnvironment({...settings, CLOUDFLARE_API_TOKEN: 'not-for-container', DEMO_MODE: 'true'});
  assert.equal(env.APP_ENV, 'production');
  assert.equal(env.DEMO_MODE, 'false');
  assert.equal(env.SEED_DEMO_DATA, 'false');
  assert.equal(env.BACKGROUND_TASKS_ENABLED, 'false');
  assert.equal(env.REGISTRATION_ENABLED, 'false');
  assert.equal(env.CORS_ORIGINS, settings.PUBLIC_ORIGIN);
  assert.equal(env.CLOUDFLARE_API_TOKEN, undefined);
});

function routingEnv(onApi, onWeb = onApi) {
  return {...settings,
    FARCARE_API: {getByName(name) {assert.equal(name, 'api'); return {fetch: onApi};}},
    FARCARE_WEB: {getByName(name) {assert.equal(name, 'web'); return {fetch: onWeb};}},
  };
}

test('same-origin proxy preserves raw webhook bytes, cookies, CSRF and signatures', async () => {
  const body = '{ "original" : "bytes" }';
  const request = new Request(`${settings.PUBLIC_ORIGIN}/backend/webhooks/photon?test=1`, {
    method: 'POST', body, headers: {'Cookie': 'session=synthetic', 'X-CSRF-Token': 'synthetic-csrf', 'Webhook-Signature': 'synthetic-signature', 'Cf-Access-Client-Secret': 'must-be-stripped'},
  });
  let called = 0;
  const response = await handlers.fetch(request, routingEnv(async forwarded => {
    called++;
    assert.equal(new URL(forwarded.url).pathname, '/webhooks/photon');
    assert.equal(new URL(forwarded.url).search, '?test=1');
    assert.equal(await forwarded.text(), body);
    assert.equal(forwarded.headers.get('Cookie'), 'session=synthetic');
    assert.equal(forwarded.headers.get('X-CSRF-Token'), 'synthetic-csrf');
    assert.equal(forwarded.headers.get('Webhook-Signature'), 'synthetic-signature');
    assert.equal(forwarded.headers.get('Cf-Access-Client-Secret'), null);
    return new Response(null, {status: 204});
  }));
  assert.equal(response.status, 204);
  assert.equal(called, 1);
});

test('SSE is streamed and distinct Set-Cookie headers survive without shared caching', async () => {
  let controller;
  const stream = new ReadableStream({start(c) {controller = c;}});
  const headers = new Headers({'Content-Type': 'text/event-stream'});
  headers.append('Set-Cookie', 'session=a; Secure; HttpOnly; Path=/');
  headers.append('Set-Cookie', 'csrf=b; Secure; Path=/');
  const response = await handlers.fetch(new Request(`${settings.PUBLIC_ORIGIN}/backend/api/events`),
    routingEnv(async () => new Response(stream, {headers})));
  assert.equal(response.headers.getSetCookie().length, 2);
  assert.equal(response.headers.get('Cache-Control'), 'private, no-store');
  assert.equal(response.headers.get('Content-Type'), 'text/event-stream');
  controller.enqueue(new TextEncoder().encode('event: timeline\ndata: {}\n\n'));
  controller.close();
  assert.match(await response.text(), /event: timeline/);
});

test('unknown origins and oversized chunked requests never reach the backend', async () => {
  let called = 0;
  const env = routingEnv(async () => {called++; return new Response('unexpected');});
  assert.equal((await handlers.fetch(new Request('https://evil.test/backend/api/me'), env)).status, 421);
  const stream = new ReadableStream({start(controller) {controller.enqueue(new Uint8Array(2 * 1024 * 1024 + 1)); controller.close();}});
  const request = new Request(`${settings.PUBLIC_ORIGIN}/backend/api/test`, {method: 'POST', body: stream, duplex: 'half'});
  assert.equal((await handlers.fetch(request, env)).status, 413);
  assert.equal(called, 0);
});

test('provider failures do not leak error details or retry a mutation', async () => {
  let called = 0;
  const response = await handlers.fetch(new Request(`${settings.PUBLIC_ORIGIN}/backend/api/requests`, {method: 'POST', body: '{}'}),
    routingEnv(async () => {called++; throw new Error('secret connection string');}));
  assert.equal(response.status, 503);
  assert.ok(!(await response.text()).includes('secret'));
  assert.equal(called, 1);
});

test('a provider RangeError is an upstream failure, not a request-size error', async () => {
  const response = await handlers.fetch(new Request(`${settings.PUBLIC_ORIGIN}/backend/api/me`),
    routingEnv(async () => {throw new RangeError('Synthetic upstream configuration failure');}));
  assert.equal(response.status, 503);
  assert.ok(!(await response.text()).includes('configuration'));
});

test('web routes use the frontend and scheduled checks wake only the API', async () => {
  let api = 0, web = 0;
  const env = routingEnv(async request => {api++; assert.equal(new URL(request.url).pathname, '/ready'); return new Response('ok');},
    async request => {web++; assert.equal(new URL(request.url).pathname, '/family'); return new Response('page');});
  await handlers.fetch(new Request(`${settings.PUBLIC_ORIGIN}/family`), env);
  await handlers.scheduled({}, env);
  await handlers.scheduled({}, {...env, CARE_AGENT_MODE: 'on_demand'});
  assert.equal(api, 1);
  assert.equal(web, 1);
});

class Base {
  constructor(ctx, env) {this.ctx = ctx; this.env = env;}
}
function containerFixture() {
  const calls = {starts: [], requests: [], timeouts: [], signals: []};
  const metadata = new Map();
  const container = {
    running: false, images: {base: 'synthetic-digest'},
    start(options) {calls.starts.push(options); this.running = true;},
    signal(sig) {calls.signals.push(sig); this.running = false;},
    async setInactivityTimeout(ms) {calls.timeouts.push(ms);},
    monitor() {return new Promise(() => {});},
    getTcpPort(port) {return {async fetch(input) {
      const request = input instanceof Request ? input : new Request(input);
      calls.requests.push({port, request});
      return new Response('ok');
    }};},
  };
  return {container, calls, metadata, ctx: {container,
    storage: {async get(key) {return metadata.get(key);}, async put(key, value) {metadata.set(key, value);}},
    waitUntil() {}, blockConcurrencyWhile(fn) {return fn();}}};
}

test('concurrent requests share one container startup and readiness check', async () => {
  const fixture = containerFixture();
  const Api = containerClass(Base, 'api');
  const api = new Api(fixture.ctx, settings);
  await Promise.all([api.fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`)), api.fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`))]);
  assert.equal(fixture.calls.starts.length, 1);
  assert.equal(fixture.calls.requests.filter(x => new URL(x.request.url).pathname === '/ready').length, 1);
  assert.equal(fixture.calls.requests.filter(x => new URL(x.request.url).pathname === '/api/me').length, 2);
  assert.equal(fixture.calls.starts[0].enableInternet, true);
  assert.equal(fixture.calls.starts[0].env.DATABASE_URL, settings.DATABASE_URL);
  assert.equal(fixture.calls.requests.at(-1).request.headers.get('X-Forwarded-Proto'), 'https');
});

test('a webhook body survives both gateway and container proxy without replay', async () => {
  const fixture = containerFixture();
  const payload = '{ "preserve" : "signature-sensitive bytes" }\n';
  let deliveries = 0;
  fixture.container.getTcpPort = () => ({async fetch(input) {
    const request = input instanceof Request ? input : new Request(input);
    if (new URL(request.url).pathname === '/ready') return new Response('ready');
    deliveries++;
    assert.equal(new URL(request.url).pathname, '/webhooks/photon');
    assert.equal(request.method, 'POST');
    assert.equal(await request.text(), payload);
    assert.equal(request.headers.get('Webhook-Signature'), 'synthetic-signature');
    return new Response(null, {status: 204});
  }});
  const Api = containerClass(Base, 'api');
  const api = new Api(fixture.ctx, settings);
  const response = await handlers.fetch(new Request(`${settings.PUBLIC_ORIGIN}/backend/webhooks/photon`, {
    method: 'POST', body: payload, headers: {'Webhook-Signature': 'synthetic-signature'},
  }), routingEnv(request => api.fetch(request)));
  assert.equal(response.status, 204);
  assert.equal(deliveries, 1);
});

test('frontend containers receive no backend secrets and cannot use internet egress', async () => {
  const fixture = containerFixture();
  const Web = containerClass(Base, 'web');
  await new Web(fixture.ctx, settings).fetch(new Request(`${settings.PUBLIC_ORIGIN}/login`));
  assert.equal(fixture.calls.starts[0].env.DATABASE_URL, undefined);
  assert.equal(fixture.calls.starts[0].env.INGEST_API_KEY, undefined);
  assert.equal(fixture.calls.starts[0].enableInternet, false);
  assert.ok(fixture.calls.requests.every(x => x.port === 3000));
});

test('containers receive the canonical host and only the edge-supplied client IP', async () => {
  const fixture = containerFixture();
  const Api = containerClass(Base, 'api');
  const api = new Api(fixture.ctx, settings);
  await api.fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`, {headers: {
    'Host': 'attacker.synthetic.test', 'Forwarded': 'host=attacker.synthetic.test',
    'X-Forwarded-For': '192.0.2.99', 'X-Real-IP': '192.0.2.99',
    'X-Forwarded-Proto': 'http', 'X-Forwarded-Host': 'attacker.synthetic.test',
    'CF-Connecting-IP': '198.51.100.42',
  }}));
  const headers = fixture.calls.requests.at(-1).request.headers;
  assert.equal(headers.get('Host'), new URL(settings.PUBLIC_ORIGIN).host);
  assert.equal(headers.get('X-Forwarded-Host'), new URL(settings.PUBLIC_ORIGIN).host);
  assert.equal(headers.get('X-Forwarded-Proto'), 'https');
  assert.equal(headers.get('X-Forwarded-For'), '198.51.100.42');
  assert.equal(headers.get('Forwarded'), null);
  assert.equal(headers.get('X-Real-IP'), null);
  await api.fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`, {headers: {'X-Forwarded-For': '192.0.2.99'}}));
  assert.equal(fixture.calls.requests.at(-1).request.headers.get('X-Forwarded-For'), null);
});

test('framework redirects preserve the public origin, API prefix, status and cookies', async () => {
  for (const [kind, location, expected] of [
    ['api', 'http://container/api/me?next=1#section', `${settings.PUBLIC_ORIGIN}/backend/api/me?next=1#section`],
    ['api', '/api/me', `${settings.PUBLIC_ORIGIN}/backend/api/me`],
    ['api', '/backend/api/me', `${settings.PUBLIC_ORIGIN}/backend/api/me`],
    ['api', 'me', `${settings.PUBLIC_ORIGIN}/backend/api/me`],
    ['web', 'http://container:3000/login', `${settings.PUBLIC_ORIGIN}/login`],
    ['web', '/login', `${settings.PUBLIC_ORIGIN}/login`],
    ['api', 'https://identity.synthetic.test/authorize', 'https://identity.synthetic.test/authorize'],
  ]) {
    const fixture = containerFixture();
    fixture.container.getTcpPort = () => ({async fetch(input) {
      const request = input instanceof Request ? input : new Request(input);
      if (['/ready', '/login'].includes(new URL(request.url).pathname)) return new Response('ready');
      const headers = new Headers({Location: location});
      headers.append('Set-Cookie', 'session=synthetic; Secure; HttpOnly; Path=/');
      headers.append('Set-Cookie', 'csrf=synthetic; Secure; Path=/');
      return new Response(null, {status: 307, headers});
    }});
    const Container = containerClass(Base, kind);
    const response = await new Container(fixture.ctx, settings).fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/old`));
    assert.equal(response.status, 307);
    assert.equal(response.headers.get('Location'), expected);
    assert.equal(response.headers.getSetCookie().length, 2);
  }
});

test('a failed startup can recover on the next request without a poisoned promise', async () => {
  const fixture = containerFixture();
  const start = fixture.container.start.bind(fixture.container);
  let failed = false;
  fixture.container.start = options => {if (!failed) {failed = true; throw new Error('Synthetic unavailable');} start(options);};
  const Api = containerClass(Base, 'api');
  const api = new Api(fixture.ctx, settings);
  await assert.rejects(api.fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`)));
  assert.equal((await api.fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`))).status, 200);
  assert.equal(fixture.calls.starts.length, 1);
});

test('redeploy with rotated credentials restarts the container and persists only a fingerprint', async () => {
  const fixture = containerFixture();
  const Api = containerClass(Base, 'api');
  await new Api(fixture.ctx, settings).fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`));
  const same = new Api(fixture.ctx, settings);
  await same.fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`));
  assert.equal(fixture.calls.starts.length, 1);
  const rotated = {...settings, INGEST_API_KEY: 'rotated-synthetic-secret'};
  await new Api(fixture.ctx, rotated).fetch(new Request(`${settings.PUBLIC_ORIGIN}/api/me`));
  assert.equal(fixture.calls.starts.length, 2);
  assert.deepEqual(fixture.calls.signals, [15]);
  assert.equal(fixture.calls.starts.at(-1).env.INGEST_API_KEY, rotated.INGEST_API_KEY);
  assert.match(fixture.metadata.get('runtime-signature'), /^[a-f0-9]{64}$/);
  assert.ok(!JSON.stringify([...fixture.metadata]).includes(rotated.INGEST_API_KEY));
});
