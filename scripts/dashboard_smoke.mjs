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
  launch(path.join(root,'apps/api/.venv/bin/python'), ['-m','app.worker'],
    path.join(root,'apps/api'), apiEnv, 'worker.log');
  const webChild = launch(process.execPath,
    [path.join(root,'apps/web/node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','13000'],
    path.join(root,'apps/web'), {...process.env,NEXT_TELEMETRY_DISABLED:'1'}, 'web.log');
  await ready(web, webChild);

  browser = await chromium.launch({headless:true});
  async function rolePage(viewport={width:1440,height:1000}) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => {
      const origin = new URL(route.request().url()).origin;
      if (origin === api || origin === web) return route.continue();
      browserErrors.push(`Unexpected external request: ${origin}`);
      return route.abort();
    });
    const tab = await context.newPage();
    tab.setDefaultTimeout(20000);
    tab.on('pageerror', error => browserErrors.push(error.message));
    tab.on('console', message => {if(message.type()==='error') browserErrors.push(message.text());});
    tab.on('response', response => {
      const url = new URL(response.url());
      if(url.origin===api) responses.push({path:url.pathname,status:response.status()});
      if(response.status()>=400) browserErrors.push(`HTTP ${response.status()}: ${url.pathname}`);
    });
    tab.on('requestfailed', request => {
      if(!closing && !request.failure()?.errorText?.includes('ERR_ABORTED')) browserErrors.push(`Request failed: ${new URL(request.url()).pathname}`);
    });
    await tab.goto(`${web}/login`, {waitUntil:'domcontentloaded'});
    return tab;
  }
  const mutation = (tab, path) => tab.waitForResponse(r => new URL(r.url()).pathname===path && r.request().method()==='POST' && r.ok());
  page = await rolePage();
  const meResponse = page.waitForResponse(response => response.url()===`${api}/api/me` && response.status()===200);
  const overviewResponse = page.waitForResponse(response => response.url()===`${api}/api/parents/parent_001/overview` && response.status()===200);
  await page.getByRole('button',{name:'Family',exact:true}).click();
  const [meResult,overviewResult] = await Promise.all([meResponse,overviewResponse]);
  const me = await meResult.json(), overview = await overviewResult.json();
  check('Browser authenticates to the actual API as the synthetic child',
    me.person.role==='child' && me.parent_ids.includes('parent_001') &&
    !meResult.request().headers().authorization &&
    (await page.context().cookies()).some(cookie => cookie.name==='farcare_session' && cookie.httpOnly));
  check('Overview comes from the API with explicitly simulated health',
    overview.parent.display_name==='Sita' && overview.health.data_mode==='simulated' && overview.health.observations.length>0);
  await page.getByRole('heading',{name:'Sita',exact:true,level:1}).waitFor();
  await page.getByRole('status').filter({hasText:/^Connected$/}).waitFor();
  check('Dashboard renders Sita and establishes the real SSE connection',true);
  await page.locator('[data-testid="live-sensors"]:visible').getByText('Watch not linked',{exact:true}).waitFor();
  const sensors = await (await page.context().request.get(`${api}/api/parents/parent_001/sensor-health`)).json();
  check('An unlinked profile never displays invented live sensor readings',
    sensors.connection_status==='not_linked' && sensors.live_heart_rate.value===null && sensors.recent_heart_rate.length===0);
  await page.screenshot({path:path.join(artifacts,'today.png'),fullPage:true});
  const nav = page.getByRole('navigation',{name:'Main'});
  await nav.getByRole('link',{name:'Health',exact:true}).click();
  await page.waitForURL(`${web}/health`);
  await page.getByText('simulated demo data',{exact:false}).first().waitFor();
  await page.getByRole('heading',{name:'Learning what is usual',exact:true}).waitFor();
  check('Health route loads and labels synthetic records',true);
  // Controlled UI fixture only. API permissions and the exact PostgreSQL query
  // are tested separately; this is never evidence of a real watch connection.
  const sensorUrl = `${api}/api/parents/parent_001/sensor-health`;
  let pulseFixture = {...sensors, connection_status:'available',
    generated_at:new Date().toISOString(),
    live_heart_rate:{...sensors.live_heart_rate,value:73,source:'ble',kind:'sample',
      freshness:'fresh',age_seconds:1,observed_at:new Date().toISOString(),message:'Synthetic browser-test sample.'},
    recent_heart_rate:[]};
  const sensorRoute = route => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(pulseFixture)});
  await page.route(sensorUrl,sensorRoute);
  const pulsePanel = page.locator('[data-testid="live-sensors"]:visible');
  await pulsePanel.getByText('Live · Fitbit BLE',{exact:true}).waitFor();
  await pulsePanel.getByText('73 bpm',{exact:true}).waitFor();
  pulseFixture = {...pulseFixture,live_heart_rate:{...pulseFixture.live_heart_rate,value:74,observed_at:new Date().toISOString()}};
  await pulsePanel.getByText('74 bpm',{exact:true}).waitFor();
  check('Synthetic sensor response updates the displayed pulse through polling',true);
  pulseFixture = {...pulseFixture,live_heart_rate:{...pulseFixture.live_heart_rate,freshness:'stale',age_seconds:80,observed_at:new Date(Date.now()-80000).toISOString()}};
  await pulsePanel.getByText('Last known reading',{exact:true}).waitFor();
  check('Stale sensor fixture loses its Live badge without fabricating a zero pulse',
    await pulsePanel.getByText('Live · Fitbit BLE',{exact:true}).count()===0 && await pulsePanel.getByText('74 bpm',{exact:true}).count()===1);
  await page.unroute(sensorUrl,sensorRoute);
  await pulsePanel.getByText('Watch not linked',{exact:true}).waitFor();
  await page.screenshot({path:path.join(artifacts,'health.png'),fullPage:true});
  await nav.getByRole('link',{name:'Care Circle',exact:true}).click();
  await page.waitForURL(`${web}/circle`);
  await page.locator('span:text-is("Asha"):visible').first().waitFor();
  check('Care Circle route displays the seeded authorized responder',true);
  await page.screenshot({path:path.join(artifacts,'circle.png'),fullPage:true});

  const parent = await rolePage({width:390,height:844});
  await parent.getByRole('button',{name:'Parent',exact:true}).click();
  await parent.getByRole('heading',{name:'Hello, Sita.',exact:true}).waitFor();
  let parentCheckPosts = 0;
  parent.on('request',r=>{if(r.method()==='POST' && new URL(r.url()).pathname==='/api/parent/check-in')parentCheckPosts++;});
  await parent.getByRole('button',{name:'I need urgent help',exact:true}).click();
  await parent.getByRole('button',{name:'Go back without sending',exact:true}).click();
  check('Cancelling urgent-help confirmation sends no alert',parentCheckPosts===0);
  const checkedIn = mutation(parent,'/api/parent/check-in');
  await parent.getByRole('button',{name:/^I'm OK/}).click();
  const savedCheck = await checkedIn;
  check('Mobile parent saves an authenticated check-in with CSRF',
    (await savedCheck.json()).latest_checkin.summary.includes('okay') &&
    Boolean(savedCheck.request().headers()['x-csrf-token']));
  await parent.getByText('Your check-in is saved.',{exact:true}).waitFor();
  await parent.locator('[data-testid="live-sensors"]:visible').getByText('Watch not linked',{exact:true}).waitFor();
  check('Parent mobile layout has no horizontal overflow',await parent.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await parent.screenshot({path:path.join(artifacts,'parent-mobile.png'),fullPage:true});

  const helper = await rolePage({width:390,height:844});
  const helperPaths = [];
  helper.on('request',request=>{if(new URL(request.url()).origin===api)helperPaths.push(new URL(request.url()).pathname);});
  await helper.getByRole('button',{name:'Helper',exact:true}).click();
  await helper.getByRole('heading',{name:'Your care requests',exact:true}).waitFor();
  await parent.getByRole('button',{name:/^Ask for help/}).click();
  await parent.getByLabel('A few details',{exact:true}).fill('Synthetic test: bring a bag of groceries.');
  const created = mutation(parent,'/api/parent/requests');
  await parent.getByRole('button',{name:'Review my request',exact:true}).click();
  const request = await (await created).json();
  check('Ordinary requests wait for parent approval',request.status==='awaiting_approval' && !request.parent_approved);
  const before = await (await helper.context().request.get(`${api}/api/responder/tasks`)).json();
  check('Helper cannot see the unapproved request',!before.tasks.some(task=>task.request_id===request.request_id));
  const approved = mutation(parent,`/api/parent/requests/${request.request_id}/decision`);
  await parent.getByRole('button',{name:'Yes, ask for help',exact:true}).click();
  check('Parent approval assigns the intended helper',(await (await approved).json()).status==='sent');
  const accepted = mutation(helper,`/api/responder/tasks/${request.request_id}/reply`);
  await helper.getByRole('button',{name:'Yes, I can help',exact:true}).click();
  check('Helper accepts a real assigned request',(await (await accepted).json()).status==='accepted');
  check('Helper mobile layout has no horizontal overflow',await helper.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await helper.screenshot({path:path.join(artifacts,'helper-mobile.png'),fullPage:true});
  const completed = mutation(helper,`/api/responder/tasks/${request.request_id}/reply`);
  await helper.getByRole('button',{name:'Mark complete',exact:true}).click();
  check('Helper completes the approved care task',(await (await completed).json()).status==='completed');
  check('Helper UI never requests private family or health routes',!helperPaths.some(p=>p.startsWith('/api/parents/')));
  const home = await (await parent.context().request.get(`${api}/api/parent/home`)).json();
  check('Parent sees completion and a running standalone worker',home.requests.some(r=>r.request_id===request.request_id && r.status==='completed') && home.agent.status==='healthy');
  const familyRequests = await (await page.context().request.get(`${api}/api/parents/parent_001/care-requests`)).json();
  check('The family account sees the completed task',familyRequests.some(r=>r.request_id===request.request_id && r.status==='completed'));
  await page.goto(`${web}/demo`,{waitUntil:'domcontentloaded'});
  const devPeople = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/demo/people' && r.ok());
  await page.getByLabel('Admin token',{exact:true}).fill('dev-admin-token');
  const devResult = await devPeople;
  check('Explicit development controls omit personal session cookies',!(await devResult.request().allHeaders()).cookie);
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'More',exact:true}).click();
  const more = page.getByRole('dialog');
  await more.getByRole('link',{name:'Family setup & invitations',exact:true}).click();
  await page.waitForURL(`${web}/setup`);
  check('Mobile family navigation opens the authenticated setup screen',true);
  await page.goto(`${web}/family`,{waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'Sita',exact:true,level:1}).waitFor();
  check('Family mobile layout has no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.screenshot({path:path.join(artifacts,'family-mobile.png'),fullPage:true});
  await page.getByRole('button',{name:'More',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Sign out',exact:true}).click();
  await page.waitForURL(`${web}/login`);
  check('Mobile sign out clears the authenticated session',!(await page.context().cookies()).some(cookie=>cookie.name==='farcare_session'));
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
console.log(JSON.stringify({status:evidence.status,checks:evidence.checks.length,artifacts,failure:evidence.failure}));
