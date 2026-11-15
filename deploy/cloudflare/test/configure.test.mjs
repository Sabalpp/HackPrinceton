import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, copyFile, readFile, readdir, stat, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {withSecretsFile, verifyDeployment} from '../deploy-tools.mjs';
import {SECRET_KEYS} from '../settings.mjs';

const source = fileURLToPath(new URL('..', import.meta.url));
const synthetic = {
  CLOUDFLARE_ACCOUNT_ID: '0'.repeat(32),
  WORKER_NAME: 'farcare-test', PUBLIC_ORIGIN: 'https://farcare-test.synthetic.workers.dev',
  DATABASE_URL: 'postgresql://synthetic:synthetic@localhost/synthetic?sslmode=require',
  INGEST_API_KEY: 'synthetic-ingest-key-for-ci-only-00000000',
};

async function isolatedCLI(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'farcare-cli-test-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const directory = path.join(root, 'deploy/cloudflare');
  await mkdir(directory, {recursive: true});
  for (const name of ['configure.mjs', 'settings.mjs', 'deploy-tools.mjs', 'target.mjs', '.env.example']) {
    await copyFile(path.join(source, name), path.join(directory, name));
  }
  return {directory, run(args, values = {}) {
    // Deliberately do not inherit developer/account credentials.
    return spawnSync(process.execPath, ['configure.mjs', ...args], {
      cwd: directory, env: {PATH: process.env.PATH, ...synthetic, ...values}, encoding: 'utf8',
    });
  }};
}

test('check validates supplied CI settings without writing a private env or generating config', async t => {
  const cli = await isolatedCLI(t);
  const before = await readdir(cli.directory);
  const result = cli.run(['--check']);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /No files changed/);
  assert.deepEqual(await readdir(cli.directory), before);
  for (const key of ['DATABASE_URL', 'INGEST_API_KEY']) assert.ok(!result.stdout.includes(synthetic[key]));
});

test('check refuses absent ingest credentials instead of silently creating a different key', async t => {
  const cli = await isolatedCLI(t);
  const result = cli.run(['--check'], {INGEST_API_KEY: ''});
  assert.equal(result.status, 1);
  assert.match(result.stderr, /INGEST_API_KEY/);
  assert.ok(!(await readdir(cli.directory)).includes('.env'));
});

test('every CLI mode refuses a Pages build before creating files', async t => {
  const cli = await isolatedCLI(t);
  const before = await readdir(cli.directory);
  for (const args of [[], ['--check'], ['--build'], ['--deploy']]) {
    const result = cli.run(args, {CF_PAGES: '1'});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Create a Worker/);
  }
  assert.deepEqual(await readdir(cli.directory), before);
  // This entry point is also used by npm preinstall/pretest for old build commands.
  const guard = spawnSync(process.execPath, ['target.mjs'], {
    cwd: cli.directory, env: {CF_PAGES: 'true'}, encoding: 'utf8',
  });
  assert.equal(guard.status, 1);
  assert.match(guard.stderr, /no static output directory/);
});

test('conflicting mode flags fail before reading or writing private settings', async t => {
  const cli = await isolatedCLI(t);
  for (const args of [['--check', '--deploy'], ['--build', '--import-local'], ['--unknown']]) {
    assert.equal(cli.run(args).status, 1);
  }
  assert.ok(!(await readdir(cli.directory)).includes('.env'));
});

test('secrets file preserves literal characters, excludes deploy credentials, and is removed', async () => {
  let filename;
  await withSecretsFile({...synthetic, GEMINI_API_KEY: 'synthetic-$literal-${variable}',
    CLOUDFLARE_API_TOKEN: 'synthetic-deploy-only'}, async file => {
    filename = file;
    assert.equal((await stat(file)).mode & 0o777, 0o600);
    assert.equal((await stat(path.dirname(file))).mode & 0o777, 0o700);
    const data = JSON.parse(await readFile(file, 'utf8'));
    assert.deepEqual(Object.keys(data), SECRET_KEYS);
    assert.equal(data.GEMINI_API_KEY, 'synthetic-$literal-${variable}');
    assert.equal(data.PHOTON_WEBHOOK_SECRET, '');
    assert.ok(!JSON.stringify(data).includes('synthetic-deploy-only'));
  });
  await assert.rejects(stat(filename), {code: 'ENOENT'});
});

test('failed publication also removes temporary secrets', async () => {
  let filename;
  await assert.rejects(withSecretsFile(synthetic, file => {
    filename = file;
    throw new Error('synthetic publication failure');
  }), /synthetic publication failure/);
  await assert.rejects(stat(filename), {code: 'ENOENT'});
});

function responses({commit = 'current', database = 'ok', ready = true, html = '<html>FarCare</html>', contentType = 'text/html', status = 200} = {}) {
  return async (url, options) => {
    assert.equal(options.redirect, 'manual');
    if (url.endsWith('/health')) return Response.json({commit, database});
    if (url.endsWith('/ready')) return Response.json({ready}, {status: ready ? 200 : 503});
    assert.ok(url.endsWith('/login'));
    return new Response(html, {status, headers: {'content-type': contentType}});
  };
}
const verify = request => verifyDeployment({origin: synthetic.PUBLIC_ORIGIN, commit: 'current', request, attempts: 1});

test('live verification requires current API revision, database, HTML app and worker readiness together', async () => {
  assert.equal(await verify(responses()), true);
  for (const options of [{commit: 'old'}, {database: 'error'}, {ready: false},
    {html: ''}, {html: '{}', contentType: 'application/json'}, {status: 404}, {status: 302}]) {
    assert.equal(await verify(responses(options)), false, JSON.stringify(options));
  }
});

test('verification retries cold-start errors but does not follow login redirects', async () => {
  let calls = 0, waits = 0;
  const request = async (...args) => {
    if (++calls === 1) throw new Error('temporary provisioning failure');
    return responses()(...args);
  };
  assert.equal(await verifyDeployment({origin: synthetic.PUBLIC_ORIGIN, commit: 'current', request,
    attempts: 2, delay: async () => { waits++; }}), true);
  assert.equal(waits, 1);
});
