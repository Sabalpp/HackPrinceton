#!/usr/bin/env node
/** Browser integration smoke. It starts only its own synthetic SQLite/console API. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createWriteStream} from 'node:fs';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const artifacts = path.resolve(process.env.SMOKE_ARTIFACT_DIR || path.join(os.tmpdir(), 'farcare-dashboard-smoke'));
const install = process.env.PLAYWRIGHT_INSTALL_DIR;
if (!install) throw new Error('Set PLAYWRIGHT_INSTALL_DIR to the temporary playwright@1.56.1 installation.');
const require = createRequire(path.join(path.resolve(install), 'package.json'));
const {chromium} = require('playwright');
const api = 'http://127.0.0.1:18000';
const web = 'http://127.0.0.1:13000';
const children = [];
const streams = [];
const browserErrors = [];
const responses = [];
const evidence = {data_mode:'simulated',status:'running',checks:[],network:'loopback API and web only'};
let browser, page, closing = false;
await fs.mkdir(artifacts, {recursive:true});
const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'farcare-smoke-db-'));

function launch(command, args, cwd, env, logName) {
  const output = createWriteStream(path.join(artifacts, logName));
  streams.push(output);
  const child = spawn(command, args, {cwd,env,stdio:['ignore','pipe','pipe']});
  child.stdout.pipe(output, {end:false});
  child.stderr.pipe(output, {end:false});
  child.on('error', error => {child.startError = error;});
  children.push(child);
  return child;
}

async function ready(url, child) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (child.startError) throw child.startError;
    if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Service exited before readiness: ${new URL(url).pathname}`);
    try {
      const result = await fetch(url, {signal:AbortSignal.timeout(2000)});
      if (result.ok) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`Service readiness timed out: ${new URL(url).pathname}`);
}

async function stop(child) {
  if (child.startError || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(resolve => child.once('exit', resolve));
  child.kill('SIGTERM');
  let timeout;
  await Promise.race([exited, new Promise(resolve => {timeout=setTimeout(resolve, 4000);})]);
  clearTimeout(timeout);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL');
    await exited;
  }
}

function check(name, condition) {
  assert(condition, name);
  evidence.checks.push(name);
}

async function run() {
  const apiEnv = {...process.env,
    APP_ENV:'test',DATABASE_URL:`sqlite:///${path.join(scratch, 'synthetic.sqlite3')}`,
    BACKGROUND_TASKS_ENABLED:'false',PROACTIVE_CHECKINS_ENABLED:'false',
    PHOTON_API_KEY:'',PHOTON_PROJECT_ID:'',PHOTON_WEBHOOK_SECRET:'',PHOTON_DELIVERY:'off',
    GEMINI_API_KEY:'',MESSAGING_CHANNEL:'console',DEMO_MODE:'true',
    SEED_DEMO_DATA:'true',SEED_HEALTH_DATA:'true',INGEST_API_KEY:'synthetic-ci-ingest-key',
    DEMO_CHILD_TOKEN:'dev-child-token',DEMO_PARENT_TOKEN:'dev-parent-token',DEMO_ADMIN_TOKEN:'dev-admin-token',
    DEMO_PARENT_HANDLE:'+15550100001',DEMO_CHILD_HANDLE:'+15550100002',
    DEMO_RESPONDER_HANDLE:'+15550100003',DEMO_RESPONDER2_HANDLE:'+15550100004',
    DEMO_EMERGENCY_NUMBER:'',CORS_ORIGINS:web,GIT_COMMIT:process.env.GITHUB_SHA || 'local-smoke',
  };
  delete apiEnv.TEST_DATABASE_URL;
  const apiChild = launch(path.join(root,'apps/api/.venv/bin/python'),
    ['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18000','--timeout-graceful-shutdown','2'],
    path.join(root,'apps/api'), apiEnv, 'api.log');
  await ready(`${api}/health`, apiChild);
  const health = await (await fetch(`${api}/health`)).json();
  check('Synthetic API is healthy, console-only, and uses fallback AI',
    health.database === 'ok' && health.messaging === 'console' && health.ai === 'fallback' && health.photon_delivery === 'off');
  const webChild = launch(process.execPath,
    [path.join(root,'apps/web/node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','13000'],
    path.join(root,'apps/web'), {...process.env,NEXT_TELEMETRY_DISABLED:'1'}, 'web.log');
  await ready(web, webChild);

  browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  // A smoke run must never load a hosted API, external fonts, or another service.
  await context.route('**/*', route => {
    const origin = new URL(route.request().url()).origin;
    if (origin === api || origin === web) return route.continue();
    browserErrors.push(`Unexpected external request: ${origin}`);
    return route.abort();
  });
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('console', message => {if(message.type()==='error') browserErrors.push(message.text());});
  page.on('response', response => {
    const url = new URL(response.url());
    if(url.origin===api) responses.push({path:url.pathname,status:response.status()});
    if(response.status()>=400) browserErrors.push(`HTTP ${response.status()}: ${url.pathname}`);
  });
  page.on('requestfailed', request => {
    if(!closing && !request.failure()?.errorText?.includes('ERR_ABORTED')) browserErrors.push(`Request failed: ${new URL(request.url()).pathname}`);
  });
  const meResponse = page.waitForResponse(response => response.url()===`${api}/api/me` && response.status()===200);
  const overviewResponse = page.waitForResponse(response => response.url()===`${api}/api/parents/parent_001/overview` && response.status()===200);
  await page.goto(web, {waitUntil:'domcontentloaded'});
  const [meResult,overviewResult] = await Promise.all([meResponse,overviewResponse]);
  const me = await meResult.json(), overview = await overviewResult.json();
  check('Browser authenticates to the actual API as the synthetic child',
    me.person.role==='child' && me.parent_ids.includes('parent_001') &&
    meResult.request().headers().authorization==='Bearer dev-child-token');
  check('Overview comes from the API with explicitly simulated health',
    overview.parent.display_name==='Sita' && overview.health.data_mode==='simulated' && overview.health.observations.length>0);
  await page.getByRole('heading',{name:'Sita',exact:true,level:1}).waitFor();
  await page.getByRole('status').filter({hasText:/^Connected$/}).waitFor();
  check('Dashboard renders Sita and establishes the real SSE connection',true);
  await page.screenshot({path:path.join(artifacts,'today.png'),fullPage:true});
  const nav = page.getByRole('navigation',{name:'Main'});
  await nav.getByRole('link',{name:'Health',exact:true}).click();
  await page.waitForURL(`${web}/health`);
  await page.getByText('simulated demo data',{exact:false}).first().waitFor();
  check('Health route loads and labels synthetic records',true);
  await page.screenshot({path:path.join(artifacts,'health.png'),fullPage:true});
  await nav.getByRole('link',{name:'Care Circle',exact:true}).click();
  await page.waitForURL(`${web}/circle`);
  await page.getByText('Asha',{exact:true}).first().waitFor();
  check('Care Circle route displays the seeded authorized responder',true);
  await page.screenshot({path:path.join(artifacts,'circle.png'),fullPage:true});
  check('No browser exceptions, console errors, failed API responses, or external requests',browserErrors.length===0);
  evidence.status='passed';
}

let deadline;
try {
  await Promise.race([run(),new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new Error('Dashboard smoke exceeded 180 seconds')),180000);})]);
} catch(error) {
  evidence.status='failed';
  evidence.failure=error.message;
  if(page) await page.screenshot({path:path.join(artifacts,'failure.png'),fullPage:true,timeout:5000}).catch(()=>{});
  process.exitCode=1;
} finally {
  clearTimeout(deadline);
  closing=true;
  if(browser) await browser.close().catch(()=>{});
  await Promise.all(children.map(stop));
  for(const stream of streams) stream.end();
  await fs.rm(scratch,{recursive:true,force:true});
  await fs.writeFile(path.join(artifacts,'result.json'),JSON.stringify({...evidence,browserErrors,responses},null,2)+'\n');
}
console.log(JSON.stringify({status:evidence.status,checks:evidence.checks.length,artifacts}));
