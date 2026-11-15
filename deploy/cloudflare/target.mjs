import {fileURLToPath} from 'node:url';
import path from 'node:path';

export function assertWorkersTarget(env = process.env) {
  if (env.CF_PAGES === '1' || env.CF_PAGES === 'true') {
    throw new Error('FarCare runs on Cloudflare Workers + Containers. This is a Pages build, which cannot run its Next.js server and Python API. Create a Worker connected to this repository; use the settings in docs/cloudflare-deployment.md. There is no static output directory.');
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { assertWorkersTarget(); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
