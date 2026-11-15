'use strict';
// Session publisher for the managed Node runtime; health_pipeline.py is the
// persistent Mac Terminal entry point. Both share an acknowledged SQLite outbox.
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {DatabaseSync} = require('node:sqlite');

const numericSQL = `INSERT INTO air_health.measurement_versions
 (observed_at,event_id,version_hash,subject_id,source,source_stream,metric,ended_at,
  local_date,value,unit,kind,attributes,source_revision_at,received_at,canonical_eligible)
 SELECT observed_at,event_id,version_hash,subject_id,source,source_stream,metric,ended_at,
  local_date,value,unit,kind,attributes,source_revision_at,received_at,canonical_eligible
 FROM jsonb_to_recordset($1::jsonb) AS x(
 observed_at timestamptz,event_id text,version_hash text,subject_id text,source text,
 source_stream text,metric text,ended_at timestamptz,local_date date,value float8,
 unit text,kind text,attributes jsonb,source_revision_at timestamptz,
 received_at timestamptz,canonical_eligible boolean)
 ON CONFLICT (observed_at,event_id,version_hash) DO NOTHING`;
const archiveSQL = `INSERT INTO air_health.source_records
 (raw_hash,source,data_type,payload,content_type,first_seen)
 SELECT raw_hash,source,data_type,decode(payload_b64,'base64'),content_type,first_seen
 FROM jsonb_to_recordset($1::jsonb) AS x(raw_hash text,source text,data_type text,
 payload_b64 text,content_type text,first_seen timestamptz)
 ON CONFLICT (raw_hash) DO NOTHING`;

function targetKey(config) {
  return crypto.createHash('sha256').update([
    config.host, String(config.port || 5432), config.database || config.dbname, config.user
  ].join('|')).digest('hex');
}
async function privateJSON(filename, data) {
  await fs.mkdir(path.dirname(filename), {recursive:true,mode:0o700});
  const tmp = filename + '.tmp';
  await fs.writeFile(tmp, JSON.stringify(data,null,2)+'\n', {mode:0o600});
  await fs.rename(tmp,filename);
}
function contentType(row) {
  if (row.data_type === 'exercise-tcx') return 'application/xml';
  try {JSON.parse(Buffer.from(row.payload).toString('utf8'));return 'application/json';}
  catch {return 'application/octet-stream';}
}
async function publish(client, dbPath, target, {batchSize=1000,maxBatches=1000}={}) {
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA busy_timeout=10000; PRAGMA foreign_keys=ON;');
  const getCursor = db.prepare('SELECT last_seq FROM deliveries WHERE target=?');
  const acknowledge = db.prepare(`INSERT INTO deliveries(target,last_seq) VALUES (?,?)
    ON CONFLICT(target) DO UPDATE SET last_seq=max(deliveries.last_seq,excluded.last_seq)`);
  const report = {acknowledged_versions:0,acknowledged_records:0};
  try {
    for (const type of ['numeric','raw']) {
      const key = target + (type === 'raw' ? ':raw' : '');
      let last = getCursor.get(key)?.last_seq || 0;
      const select = db.prepare(type === 'numeric'
        ? 'SELECT seq,payload FROM measurement_versions WHERE seq>? ORDER BY seq LIMIT ?'
        : 'SELECT rowid,raw_hash,source,data_type,payload,first_seen FROM raw_records WHERE rowid>? ORDER BY rowid LIMIT ?');
      for(let batch=0;batch<maxBatches;batch++) {
        const rows = select.all(last,batchSize);
        if (!rows.length) break;
        const values = rows.map(row=>type === 'numeric' ? JSON.parse(row.payload) : ({
          raw_hash:row.raw_hash,source:row.source,data_type:row.data_type,
          payload_b64:Buffer.from(row.payload).toString('base64'),
          content_type:contentType(row),first_seen:row.first_seen
        }));
        await client.query('BEGIN');
        try {
          await client.query(type === 'numeric' ? numericSQL : archiveSQL,[JSON.stringify(values)]);
          await client.query('COMMIT');
        } catch (error) {
          await client.query('ROLLBACK').catch(()=>{});
          throw error;
        }
        last = rows[rows.length-1][type==='numeric'?'seq':'rowid'];
        // A crash here intentionally replays the already-committed remote batch.
        acknowledge.run(key,last);
        report[type==='numeric'?'acknowledged_versions':'acknowledged_records']+=rows.length;
      }
      report[type==='numeric'?'last_seq':'last_rowid']=last;
    }
    report.local_versions=db.prepare('SELECT count(*) AS n FROM measurement_versions').get().n;
    report.local_current_events=db.prepare('SELECT count(*) AS n FROM current_events').get().n;
    report.local_raw_records=db.prepare('SELECT count(*) AS n FROM raw_records').get().n;
    report.pending_versions=db.prepare('SELECT count(*) AS n FROM measurement_versions WHERE seq>?').get(report.last_seq).n;
    report.pending_raw_records=db.prepare('SELECT count(*) AS n FROM raw_records WHERE rowid>?').get(report.last_rowid).n;
    return report;
  } finally {db.close();}
}
function start({connect,dbPath,target,statusFile,intervalMs=1000}) {
  if (!Number.isFinite(intervalMs) || intervalMs < 1) throw new RangeError('intervalMs must be positive');
  let stopped=false,timer=null,client=null,active=null;
  const state={mode:'managed_session',state:'starting',poll_interval_ms:intervalMs};
  async function tick() {
    if(stopped) return;
    const started=performance.now();
    try {
      if(!client) {client=await connect();client.on('error',()=>{});}
      Object.assign(state,await publish(client,dbPath,target),{
        state:'connected',last_success:new Date().toISOString(),error_type:null
      });
    } catch(error) {
      Object.assign(state,{state:'retry_pending',error_type:error.code||error.name});
      if(client) await client.end().catch(()=>{});
      client=null;
    }
    state.checked_at=new Date().toISOString();
    state.cycle_ms=Math.round(performance.now()-started);
    await privateJSON(statusFile,state).catch(()=>{});
    // Count query time toward the target cadence, without overlapping transactions.
    if(!stopped) {timer=setTimeout(()=>{active=tick();},Math.max(0,intervalMs-(performance.now()-started)));timer.unref();}
  }
  const first=active=tick();
  return {state,first,async stop(){stopped=true;clearTimeout(timer);await active;
    if(client)await client.end();state.state='stopped';await privateJSON(statusFile,state);}};
}
module.exports={publish,start,targetKey,privateJSON,numericSQL,archiveSQL};
