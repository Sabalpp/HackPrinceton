export const SECRET_KEYS = [
  'DATABASE_URL', 'INGEST_API_KEY', 'PHOTON_API_KEY', 'PHOTON_PROJECT_ID',
  'PHOTON_WEBHOOK_SECRET', 'GEMINI_API_KEY',
];
export const APP_KEYS = [
  'SESSION_HOURS', 'MESSAGING_CHANNEL', 'PROACTIVE_CHECKINS_ENABLED', 'GEMINI_MODEL',
  'WORKER_INTERVAL_SECONDS', 'WORKER_BATCH_SIZE', 'WORKER_MAX_DELIVERY_ATTEMPTS',
  'WORKER_RETRY_BASE_SECONDS', 'WORKER_RETRY_MAX_SECONDS', 'WORKER_HEARTBEAT_STALE_SECONDS',
  'TIGER_SYNC_ENABLED', 'TIGER_SUBJECT_ID', 'TIGER_PARENT_ID', 'TIGER_SYNC_SECONDS',
  'TIGER_LOOKBACK_DAYS',
];
export const INSTANCE_TYPES = ['basic', 'standard-1', 'standard-2'];

export function validateSettings(settings, {secrets = true} = {}) {
  const errors = [];
  const s = settings;
  if (!/^[a-z][a-z0-9-]{0,62}$/.test(s.WORKER_NAME || '')) errors.push('WORKER_NAME must be a lowercase Worker name.');
  if (!/^[a-f0-9]{32}$/i.test(s.CLOUDFLARE_ACCOUNT_ID || '')) errors.push('Set the 32-character CLOUDFLARE_ACCOUNT_ID from your dashboard.');
  let origin;
  try {
    origin = new URL(s.PUBLIC_ORIGIN);
    if (origin.protocol !== 'https:' || origin.origin !== s.PUBLIC_ORIGIN || origin.username || origin.password || origin.port || !origin.hostname.includes('.') || /[*.]/.test(origin.hostname[0])) throw new Error();
    if (origin.hostname.endsWith('.example.com') || origin.hostname.endsWith('.invalid')) throw new Error();
  } catch { errors.push('PUBLIC_ORIGIN must be your exact HTTPS origin, without a path or trailing slash.'); }
  for (const key of ['CUSTOM_DOMAIN', 'PROACTIVE_CHECKINS_ENABLED', 'TIGER_SYNC_ENABLED']) {
    if (!['true', 'false'].includes(s[key])) errors.push(`${key} must be true or false.`);
  }
  if (origin && s.CUSTOM_DOMAIN === 'false' && (!origin.hostname.endsWith('.workers.dev') || !origin.hostname.startsWith(`${s.WORKER_NAME}.`) || origin.hostname.split('.').length !== 4)) errors.push('The workers.dev address must match WORKER_NAME and your account subdomain.');
  if (origin && s.CUSTOM_DOMAIN === 'true' && origin.hostname.endsWith('.workers.dev')) errors.push('CUSTOM_DOMAIN=true requires your own Cloudflare-managed domain.');
  if (origin && origin.hostname.endsWith('.pages.dev')) errors.push('This app runs on Workers with Containers, not Pages. Use your workers.dev address or your own domain.');
  for (const key of ['API_INSTANCE_TYPE', 'WEB_INSTANCE_TYPE']) {
    if (!INSTANCE_TYPES.includes(s[key])) errors.push(`${key} must be ${INSTANCE_TYPES.join(', ')}.`);
  }
  if (!['always_on', 'on_demand'].includes(s.CARE_AGENT_MODE)) errors.push('CARE_AGENT_MODE must be always_on or on_demand.');
  if (!['console', 'photon'].includes(s.MESSAGING_CHANNEL)) errors.push('MESSAGING_CHANNEL must be console or photon.');
  if (s.MESSAGING_CHANNEL === 'photon' && s.CARE_AGENT_MODE !== 'always_on') errors.push('Photon delivery requires always_on so due care work is not suspended.');
  if (s.PROACTIVE_CHECKINS_ENABLED === 'true' && (s.MESSAGING_CHANNEL !== 'photon' || s.CARE_AGENT_MODE !== 'always_on')) errors.push('Proactive check-ins require Photon and always_on.');
  const ranges = {
    SESSION_HOURS: [1, 168], WORKER_INTERVAL_SECONDS: [1, 60], WORKER_BATCH_SIZE: [1, 100],
    WORKER_MAX_DELIVERY_ATTEMPTS: [1, 10], WORKER_RETRY_BASE_SECONDS: [1, 300],
    WORKER_RETRY_MAX_SECONDS: [1, 3600], WORKER_HEARTBEAT_STALE_SECONDS: [30, 3600],
    TIGER_SYNC_SECONDS: [15, 3600], TIGER_LOOKBACK_DAYS: [1, 90],
  };
  for (const [key, [min, max]] of Object.entries(ranges)) {
    const n = Number(s[key]);
    if (!Number.isInteger(n) || n < min || n > max) errors.push(`${key} must be an integer from ${min} to ${max}.`);
  }
  if (Number(s.WORKER_RETRY_MAX_SECONDS) < Number(s.WORKER_RETRY_BASE_SECONDS)) errors.push('Retry maximum must not be less than the base delay.');
  if (s.TIGER_SYNC_ENABLED === 'true' && (!s.TIGER_SUBJECT_ID?.trim() || !s.TIGER_PARENT_ID?.trim())) errors.push('Tiger sync requires both the real subject ID and the FarCare parent ID.');
  if (secrets) {
    try {
      const db = new URL(s.DATABASE_URL?.replace(/^postgresql\+psycopg:/, 'postgresql:'));
      if (!['postgres:', 'postgresql:'].includes(db.protocol) || !db.hostname || !db.username || !db.password || !db.pathname.slice(1) || !['require', 'verify-ca', 'verify-full'].includes(db.searchParams.get('sslmode'))) throw new Error();
    } catch { errors.push('DATABASE_URL must be a complete PostgreSQL URL with sslmode=require (or verify-full/verify-ca).'); }
    if ((s.INGEST_API_KEY || '').length < 32) errors.push('INGEST_API_KEY needs at least 32 characters.');
    if (s.MESSAGING_CHANNEL === 'photon') for (const key of ['PHOTON_API_KEY', 'PHOTON_PROJECT_ID', 'PHOTON_WEBHOOK_SECRET']) {
      if (!s[key]?.trim()) errors.push(`Set ${key} before enabling Photon.`);
    }
  }
  return errors;
}

export function runtimeVariables(s, commit) {
  return {
    PUBLIC_ORIGIN: s.PUBLIC_ORIGIN, API_INSTANCE_TYPE: s.API_INSTANCE_TYPE,
    WEB_INSTANCE_TYPE: s.WEB_INSTANCE_TYPE, CARE_AGENT_MODE: s.CARE_AGENT_MODE,
    ...Object.fromEntries(APP_KEYS.map(key => [key, s[key] ?? ''])),
    GIT_COMMIT: commit,
  };
}

export function wranglerConfig(s, commit) {
  const config = {
    $schema: './node_modules/wrangler/config-schema.json',
    name: s.WORKER_NAME, account_id: s.CLOUDFLARE_ACCOUNT_ID,
    main: 'src/worker.mjs', compatibility_date: '2026-11-14',
    workers_dev: s.CUSTOM_DOMAIN !== 'true', preview_urls: false,
    // Avoid logging URLs containing auth tokens or health/request identifiers.
    observability: {enabled: false},
    vars: runtimeVariables(s, commit),
    secrets: {required: ['DATABASE_URL', 'INGEST_API_KEY',
      ...(s.MESSAGING_CHANNEL === 'photon' ? ['PHOTON_API_KEY', 'PHOTON_PROJECT_ID', 'PHOTON_WEBHOOK_SECRET'] : [])]},
    containers: [
      {class_name: 'FarCareApi', scheduling_policy: 'durable_object',
        images: {base: {dockerfile: './Dockerfile.api', build_context: '../..'}},
        observability: {enabled: false}},
      {class_name: 'FarCareWeb', scheduling_policy: 'durable_object',
        images: {base: {dockerfile: '../Dockerfile.web', build_context: '../../apps/web',
          build_vars: {NEXT_PUBLIC_API_URL: '/backend'}}},
        observability: {enabled: false}},
    ],
    durable_objects: {bindings: [
      {name: 'FARCARE_API', class_name: 'FarCareApi'},
      {name: 'FARCARE_WEB', class_name: 'FarCareWeb'},
    ]},
    exports: {FarCareApi: {type: 'durable-object', storage: 'sqlite'},
      FarCareWeb: {type: 'durable-object', storage: 'sqlite'}},
    triggers: {crons: s.CARE_AGENT_MODE === 'always_on' ? ['* * * * *'] : []},
  };
  if (s.CUSTOM_DOMAIN === 'true') config.routes = [{pattern: new URL(s.PUBLIC_ORIGIN).hostname, custom_domain: true}];
  return config;
}

export function apiEnvironment(env) {
  return {
    ...Object.fromEntries([...APP_KEYS, ...SECRET_KEYS, 'GIT_COMMIT'].filter(k => env[k] !== undefined).map(k => [k, String(env[k])])),
    APP_ENV: 'production', CORS_ORIGINS: env.PUBLIC_ORIGIN,
    DEMO_MODE: 'false', SEED_DEMO_DATA: 'false', SEED_HEALTH_DATA: 'false',
    BACKGROUND_TASKS_ENABLED: 'false', REGISTRATION_ENABLED: 'false',
    PHOTON_DELIVERY: env.MESSAGING_CHANNEL === 'photon' ? 'webhook' : 'off',
    PORT: '8000',
  };
}
