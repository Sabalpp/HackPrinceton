import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {SECRET_KEYS} from './settings.mjs';

// JSON avoids dotenv's $VARIABLE expansion. Only application secrets are sent;
// the Cloudflare deployment token must never become a runtime binding.
export async function withSecretsFile(settings, callback) {
  const directory = await mkdtemp(path.join(tmpdir(), 'farcare-deploy-'));
  try {
    const filename = path.join(directory, 'secrets.json');
    const secrets = Object.fromEntries(SECRET_KEYS.map(key => [key, settings[key] || '']));
    await writeFile(filename, JSON.stringify(secrets), {mode: 0o600});
    return await callback(filename);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
}

export async function verifyDeployment({origin, commit, request = fetch,
  attempts = 90, delay = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
  const options = () => ({signal: AbortSignal.timeout(10000), redirect: 'manual'});
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const healthResponse = await request(`${origin}/backend/health`, options());
      const health = await healthResponse.json();
      if (healthResponse.ok && health.database === 'ok' && health.commit === commit) {
        const web = await request(`${origin}/login`, options());
        const html = await web.text();
        // An edge error, redirect, JSON stub or empty upload is not the app.
        const isPage = web.ok && /text\/html/i.test(web.headers.get('content-type') || '') && /<html[\s>]/i.test(html);
        if (isPage) {
          const readyResponse = await request(`${origin}/backend/ready`, options());
          const ready = await readyResponse.json();
          if (readyResponse.ok && ready.ready === true) return true;
        }
      }
    } catch { /* Provisioning may still be in progress; never print responses or credentials. */ }
    if (attempt + 1 < attempts) await delay(3000);
  }
  return false;
}
