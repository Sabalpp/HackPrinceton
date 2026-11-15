'use strict';
// No PostgreSQL connection is made; the publisher reads a temporary SQLite DB.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const publisher = require('../runtime/tiger_publisher.cjs');

function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
}

function pause(ms) {return new Promise(resolve => setTimeout(resolve, ms));}

async function bounded(promise) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Synthetic lifecycle test timed out')), 2000);
    })]);
  } finally {clearTimeout(timer);}
}

async function fixture(callback) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'synthetic-publisher-'));
  const dbPath = path.join(root, 'outbox.sqlite3');
  const db = new DatabaseSync(dbPath);
  db.exec(`CREATE TABLE deliveries(target TEXT PRIMARY KEY,last_seq INTEGER);
    CREATE TABLE measurement_versions(seq INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE current_events(event_id TEXT PRIMARY KEY,seq INTEGER);
    CREATE TABLE raw_records(raw_hash TEXT PRIMARY KEY,source TEXT,data_type TEXT,
      payload BLOB,first_seen TEXT);`);
  db.close();
  try {await callback({root, dbPath, statusFile: path.join(root, 'status.json')});}
  finally {await fs.rm(root, {recursive:true, force:true});}
}

function append(dbPath) {
  const db = new DatabaseSync(dbPath);
  const payload = {event_id:'synthetic-event',version_hash:'synthetic-version',
    observed_at:'2026-11-14T18:00:00Z',source:'ble',subject_id:'synthetic',
    source_stream:'synthetic-stream',metric:'heart_rate_bpm',ended_at:null,
    local_date:'2026-11-14',value:78,unit:'bpm',kind:'sample',attributes:{},
    source_revision_at:'2026-11-14T18:00:00Z',received_at:'2026-11-14T18:00:00Z',
    canonical_eligible:true};
  db.prepare('INSERT INTO measurement_versions VALUES (?,?)').run(1, JSON.stringify(payload));
  db.prepare('INSERT INTO current_events VALUES (?,?)').run(payload.event_id, 1);
  db.close();
}

async function runTests() {
  const passed = [];
  await fixture(async ({dbPath}) => {
    append(dbPath);
    let fail = true, commits = 0, rollbacks = 0;
    const client = {async query(sql){
      if (sql === publisher.numericSQL && fail) throw new Error('Synthetic network failure');
      if (sql === 'COMMIT') commits += 1;
      if (sql === 'ROLLBACK') rollbacks += 1;
      return {rows:[]};
    }};
    await assert.rejects(publisher.publish(client,dbPath,'synthetic-target'), /Synthetic network failure/);
    const db = new DatabaseSync(dbPath);
    try {assert.equal(db.prepare('SELECT count(*) AS n FROM deliveries').get().n,0);}
    finally {db.close();}
    assert.equal(rollbacks,1);
    assert.equal(commits,0);
    fail = false;
    const retried = await publisher.publish(client,dbPath,'synthetic-target');
    assert.equal(retried.acknowledged_versions,1);
    assert.equal(retried.pending_versions,0);
    const replay = await publisher.publish(client,dbPath,'synthetic-target');
    assert.equal(replay.acknowledged_versions,0);
    assert.equal(commits,1);
    passed.push('failed writes remain queued, retry commits them once, then replay is empty');
  });
  await fixture(async ({dbPath,statusFile}) => {
    const entered = deferred(), release = deferred();
    let ends = 0, firstBegin = true;
    const client = {on(){}, async end(){ends += 1;}, async query(sql){
      if (sql === 'BEGIN' && firstBegin) {
        firstBegin = false;
        entered.resolve();
        await release.promise;
      }
      return {rows:[]};
    }};
    const handle = publisher.start({connect:async()=>client,dbPath,target:'synthetic-target',statusFile,intervalMs:1});
    let stopped;
    try {
      await bounded(handle.first);  // First tick has no records.
      append(dbPath);
      await bounded(entered.promise); // A later tick is inside a remote transaction.
      let resolved = false;
      stopped = handle.stop().then(()=>{resolved=true;});
      await pause(10);
      const stoppedBeforeRelease = resolved;
      release.resolve();
      await bounded(stopped);
      await pause(10);
      assert.equal(stoppedBeforeRelease,false,'stop must await the currently running timer tick');
      assert.equal(ends,1);
      assert.equal(handle.state.state,'stopped');
      assert.equal(JSON.parse(await fs.readFile(statusFile,'utf8')).state,'stopped');
      const db = new DatabaseSync(dbPath);
      assert.equal(db.prepare('SELECT last_seq FROM deliveries WHERE target=?').get('synthetic-target').last_seq,1);
      db.close();
      passed.push('stop awaits a later in-flight publish and preserves final stopped status');
    } finally {
      release.resolve();
      await bounded(stopped || handle.stop());
      await pause(10);
    }
  });

  await fixture(async ({dbPath,statusFile}) => {
    const reconnecting = deferred(), releaseConnect = deferred();
    let calls = 0, ends = 0;
    const client = {on(){},async end(){ends += 1;},async query(){return {rows:[]};}};
    const handle = publisher.start({dbPath,target:'synthetic-target',statusFile,intervalMs:1,
      connect:async()=>{
        calls += 1;
        if (calls === 1) throw new Error('Synthetic initial connection failure');
        reconnecting.resolve();
        await releaseConnect.promise;
        return client;
      }});
    let stopped;
    try {
      await bounded(handle.first);
      await bounded(reconnecting.promise);
      let resolved = false;
      stopped = handle.stop().then(()=>{resolved=true;});
      await pause(10);
      const stoppedBeforeReconnect = resolved;
      releaseConnect.resolve();
      await bounded(stopped);
      await pause(10);
      assert.equal(stoppedBeforeReconnect,false,'stop must await an in-flight reconnect');
      assert.equal(ends,1,'the client returned after stop was requested must be closed');
      assert.equal(handle.state.state,'stopped');
      assert.equal(JSON.parse(await fs.readFile(statusFile,'utf8')).state,'stopped');
      passed.push('stop awaits an in-flight reconnect and closes the resulting client');
    } finally {
      releaseConnect.resolve();
      await bounded(stopped || handle.stop());
      await pause(10);
    }
  });
  return {passed:passed.length,tests:passed};
}

module.exports={runTests};
if(require.main===module){runTests().then(result=>console.log(JSON.stringify(result)),error=>{console.error(error);process.exitCode=1;});}
