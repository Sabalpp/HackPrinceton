import {apiEnvironment, INSTANCE_TYPES} from '../settings.mjs';

const IDLE_MS = 5 * 60 * 1000;
const MAX_BODY = 2 * 1024 * 1024;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const unavailable = () => new Response('FarCare is temporarily unavailable. Please try again.', {
  status: 503, headers: {'Cache-Control': 'no-store', 'Retry-After': '10'},
});

export function containerClass(Base, kind) {
  const portNumber = kind === 'api' ? 8000 : 3000;
  const probePath = kind === 'api' ? '/ready' : '/login';
  return class extends Base {
    constructor(ctx, env) {
      super(ctx, env);
      this.starting = null;
      this.monitoring = null;
      this.ready = false;
      this.configChecked = false;
      if (ctx.container?.running) ctx.blockConcurrencyWhile(() => ctx.container.setInactivityTimeout(IDLE_MS));
    }
    async ensureReady() {
      const container = this.ctx.container;
      if (!container) throw new Error('Container binding missing');
      const instance = this.env[kind === 'api' ? 'API_INSTANCE_TYPE' : 'WEB_INSTANCE_TYPE'];
      if (!INSTANCE_TYPES.includes(instance)) throw new Error('Invalid container size');
      const runtimeEnv = kind === 'api' ? apiEnvironment(this.env) : {NODE_ENV: 'production', NEXT_TELEMETRY_DISABLED: '1'};
      if (kind === 'api' && (!this.env.DATABASE_URL || !this.env.INGEST_API_KEY || !this.env.PUBLIC_ORIGIN)) throw new Error('Application secrets are not configured');
      if (!this.configChecked) {
        const bytes = new TextEncoder().encode(JSON.stringify([container.images.base, instance, runtimeEnv]));
        const signature = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
        if (container.running && await this.ctx.storage.get('runtime-signature') !== signature) {
          // Environment changes (including secret rotation) must reach an already
          // running container. Give supervised processes time to stop cleanly.
          container.signal(15);
          for (let attempt = 0; container.running && attempt < 120; attempt++) await delay(250);
          if (container.running) await container.destroy('Deployment configuration changed');
          this.ready = false;
        }
        this.runtimeSignature = signature;
      }
      if (!container.running) {
        container.start({image: container.images.base, instance, enableInternet: kind === 'api',
          env: runtimeEnv});
        await this.ctx.storage.put('runtime-signature', this.runtimeSignature);
        this.ready = false;
      }
      this.configChecked = true;
      await container.setInactivityTimeout(IDLE_MS);
      if (!this.monitoring) {
        this.monitoring = container.monitor().catch(() => {}).finally(() => {
          this.ready = false;
          this.monitoring = null;
        });
        this.ctx.waitUntil(this.monitoring);
      }
      if (this.ready) return;
      for (let attempt = 0; attempt < 40; attempt++) {
        try {
          const response = await container.getTcpPort(portNumber).fetch(`http://container${probePath}`, {signal: AbortSignal.timeout(1000)});
          const healthy = response.ok;
          await response.body?.cancel();
          if (healthy) {this.ready = true; return;}
        } catch { /* Probe only; never replay an incoming mutation. */ }
        await delay(250);
      }
      throw new Error('Container readiness timed out');
    }
    async fetch(request) {
      this.starting ??= this.ensureReady().finally(() => {this.starting = null;});
      await this.starting;
      const url = new URL(request.url);
      const host = url.host;
      url.protocol = 'http:';
      url.host = 'container';
      const forwarded = new Request(url, request);
      forwarded.headers.delete('host');
      forwarded.headers.delete('forwarded');
      forwarded.headers.delete('x-forwarded-for');
      forwarded.headers.set('X-Forwarded-Proto', 'https');
      forwarded.headers.set('X-Forwarded-Host', host);
      // Stream the response intact, including SSE and multiple Set-Cookie headers.
      return this.ctx.container.getTcpPort(portNumber).fetch(forwarded);
    }
  };
}

async function boundedBody(request) {
  if (!request.body) return null;
  const reader = request.body.getReader();
  const parts = [];
  let bytes = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_BODY) {await reader.cancel(); throw new RangeError('Body too large');}
      parts.push(value);
    }
  } finally {reader.releaseLock();}
  const result = new Uint8Array(bytes);
  let offset = 0;
  for (const part of parts) {result.set(part, offset); offset += part.length;}
  return result;
}

export const handlers = {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.origin !== env.PUBLIC_ORIGIN) return new Response('Unknown application origin', {status: 421, headers: {'Cache-Control': 'no-store'}});
    const isApi = url.pathname === '/backend' || url.pathname.startsWith('/backend/');
    if (isApi) {
      const size = Number(request.headers.get('content-length') || 0);
      if (size > MAX_BODY) return new Response('Request too large', {status: 413});
      url.pathname = url.pathname.slice('/backend'.length) || '/';
    }
    try {
      const headers = new Headers(request.headers);
      // Never forward Cloudflare Access credentials to the application.
      headers.delete('cf-access-client-id');
      headers.delete('cf-access-client-secret');
      let body;
      if (!['GET', 'HEAD'].includes(request.method)) body = await boundedBody(request);
      const forwarded = new Request(url, {method: request.method, headers, body, redirect: 'manual', signal: request.signal});
      const stub = isApi ? env.FARCARE_API.getByName('api') : env.FARCARE_WEB.getByName('web');
      const response = await stub.fetch(forwarded);
      // No shared caching of account pages, API responses, cookies or health data.
      const result = new Response(response.body, response);
      result.headers.set('Cache-Control', 'private, no-store');
      result.headers.set('X-Content-Type-Options', 'nosniff');
      result.headers.set('Referrer-Policy', 'no-referrer');
      result.headers.set('Strict-Transport-Security', 'max-age=31536000');
      return result;
    } catch (error) {
      if (error instanceof RangeError) return new Response('Request too large', {status: 413});
      return unavailable(); // Never include exceptions containing credentials in public errors.
    }
  },
  async scheduled(_event, env) {
    if (env.CARE_AGENT_MODE !== 'always_on') return;
    const response = await env.FARCARE_API.getByName('api').fetch(new Request(`${env.PUBLIC_ORIGIN}/ready`));
    await response.body?.cancel();
    if (!response.ok) throw new Error('FarCare API heartbeat failed');
  },
};
