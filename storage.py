"""Persistent masked curation state, optimistic concurrency and immutable releases.

SQLite by default; DATABASE_URL also accepts postgresql:// / postgres:// URLs
(using psycopg). No inference API key or original uploaded bytes are stored.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (JSON, Column, ForeignKey, Integer, LargeBinary, MetaData,
                        String, Table, Text, create_engine, delete, event, insert,
                        select, update)
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1
metadata = MetaData()
versions = Table('schema_versions', metadata, Column('version', Integer, primary_key=True))
batches = Table('batches', metadata, Column('id', String(64), primary_key=True), Column('revision', Integer, nullable=False), Column('snapshot', JSON, nullable=False), Column('context', JSON, nullable=False), Column('created_at', String(40), nullable=False), Column('updated_at', String(40), nullable=False))
records = Table('records', metadata, Column('id', String(140), primary_key=True), Column('batch_id', String(64), ForeignKey('batches.id'), nullable=False, index=True), Column('local_id', String(64), nullable=False), Column('revision', Integer, nullable=False), Column('status', String(32), nullable=False, index=True), Column('data', JSON, nullable=False))
releases = Table('releases', metadata, Column('key', String(250), primary_key=True), Column('batch_id', String(64), ForeignKey('batches.id'), nullable=False), Column('gold_version', String(80), nullable=False), Column('release_id', String(80), nullable=False), Column('created_at', String(40), nullable=False), Column('manifest', JSON, nullable=False), Column('archive', LargeBinary, nullable=False), Column('sha256', String(64), nullable=False))
outbox = Table('webhook_outbox', metadata, Column('id', String(64), primary_key=True), Column('event_type', String(80), nullable=False), Column('payload', JSON, nullable=False), Column('status', String(20), nullable=False), Column('attempts', Integer, nullable=False), Column('next_attempt', Integer, nullable=False), Column('lease', String(64)), Column('created_at', String(40), nullable=False))


bronze_registry = Table('bronze_registry', metadata, Column('batch_id', String(64), primary_key=True), Column('sha256', String(64), primary_key=True), Column('filename', String(300), nullable=False), Column('bytes', Integer, nullable=False), Column('ingested_at', String(40), nullable=False), Column('registered_at', String(40), nullable=False))


class ConflictError(ValueError):
    """The caller's snapshot is stale; reload instead of overwriting another review."""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def database_url(value=None):
    value = value or os.getenv('DATABASE_URL') or f'sqlite:///{ROOT / "watan_gold_studio.db"}'
    if value.startswith('postgres://'):
        value = 'postgresql+psycopg://' + value[len('postgres://'):]
    elif value.startswith('postgresql://'):
        value = 'postgresql+psycopg://' + value[len('postgresql://'):]
    if not value.startswith(('sqlite:///', 'sqlite://', 'postgresql+psycopg://')):
        raise ValueError('DATABASE_URL must be a SQLite or PostgreSQL database connection URL, not a Supabase HTTP API URL.')
    return value


def safe_context(context):
    """Persist public verifier context and release/input preferences, never API keys."""
    import watan_gold_studio as w
    public = context.get('config', {})
    permitted = {'base_url', 'model', 'mode', 'timeout', 'max_tokens', 'target'}
    config = w.QwenConfig(**{k: v for k, v in public.items() if k in permitted})
    policy = copy.deepcopy(context.get('policy', {}))
    if not isinstance(policy, dict) or not policy.get('product_name') or w.pii_leaks(w.canonical_json(policy)):
        raise ValueError('A masked, valid project policy is required for persistence.')
    preferences = {k: copy.deepcopy(context[k]) for k in ('gold_version', 'holdout_enabled', 'holdout_pct', 'include_conversations', 'overlap', 'embedding_model', 'prompt_column', 'answer_column', 'default_origin') if k in context}
    return {'config': config.public(), 'policy': policy, **preferences}


def safe_batch(batch):
    import watan_gold_studio as w
    clean = copy.deepcopy({k: batch[k] for k in ('records', 'sources', 'issues', 'file_aliases', 'ingested_at', 'silver_run_id', 'review_packs', 'bronze', 'silver') if k in batch})
    for entry in clean.get('bronze', []):
        if not (isinstance(entry, dict) and re.fullmatch(r'[0-9a-f]{64}', str(entry.get('sha256', ''))) and isinstance(entry.get('bytes'), int) and isinstance(entry.get('filename'), str) and isinstance(entry.get('ingested_at'), str)):
            raise ValueError('Bronze entries need filename, sha256, bytes and ingested_at.')
        entry['filename'] = w.mask_pii(entry['filename'])[0][:300]
    clean.setdefault('records', []);clean.setdefault('sources', []);clean.setdefault('issues', [])
    ids = set()
    for record in clean['records']:
        rid = record.get('id')
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', rid) or rid in ids:
            raise ValueError('Record IDs must be unique within a batch.')
        ids.add(rid)
        allowed = record.get('synthetic_pii_values', [])
        for key in ('original', 'candidate'):
            if record.get(key):w.validate_safe(record[key], allowed)
        for key in ('original_text', 'text'):
            if record.get(key):w.validate_prose(record[key])
        verification = record.get('verification')
        if verification:
            result = verification['result']
            if result.get('messages'):w.validate_safe(result['messages'], allowed)
            if result.get('text'):w.validate_prose(result['text'])
        def masked_meta(value):
            if isinstance(value,str):return w.mask_pii(value)[0]
            if isinstance(value,list):return [masked_meta(v) for v in value]
            if isinstance(value,dict):return {k:masked_meta(v) for k,v in value.items()}
            return value
        if 'meta' in record:record['meta']=masked_meta(record['meta'])
        if verification:
            for key in ('reason','domain','dbp_errors','policy_errors','factual_errors'):
                if key in verification['result']:verification['result'][key]=masked_meta(verification['result'][key])
        record['reason'] = w.mask_pii(record.get('reason', ''))[0]
        for event_row in record.get('history', []):
            if event_row.get('reviewer_notes'):event_row['reviewer_notes'] = w.mask_pii(event_row['reviewer_notes'])[0]
    # Force JSON-only snapshots and disallow NaN/custom objects before any DB write.
    return json.loads(json.dumps(clean, ensure_ascii=False, allow_nan=False))


class Storage:
    def __init__(self, url=None, release_root=None):
        self.url = database_url(url)
        self.release_root = Path(release_root or os.getenv('RELEASES_DIR') or ROOT / 'releases').resolve()
        opts = {'future': True, 'pool_pre_ping': True, 'hide_parameters': True}
        if self.url.startswith('sqlite:'):
            opts['connect_args'] = {'check_same_thread': False, 'timeout': 30}
            if self.url.endswith(':memory:') or self.url in {'sqlite://', 'sqlite:///'}:
                opts['poolclass'] = StaticPool
        self.engine = create_engine(self.url, **opts)
        if self.engine.dialect.name == 'sqlite':
            @event.listens_for(self.engine, 'connect')
            def sqlite_setup(dbapi_connection, _):
                cursor = dbapi_connection.cursor()
                cursor.execute('PRAGMA foreign_keys=ON')
                cursor.execute('PRAGMA busy_timeout=30000')
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.close()
        if self.engine.dialect.name == 'postgresql':
            schema = os.getenv('DATABASE_SCHEMA', 'warisan')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', schema) or schema.lower() in {'public','information_schema','pg_catalog'}:
                raise ValueError('Use a dedicated non-exposed DATABASE_SCHEMA, not public/system schemas.')
            # Keep Supabase curation tables outside its default public Data API.
            with self.engine.begin() as conn:
                conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self.engine.update_execution_options(schema_translate_map={None:schema})
        metadata.create_all(self.engine)
        self._bronze_triggers()
        with self.write() as conn:
            found = conn.execute(select(versions.c.version)).scalars().all()
            if not found:conn.execute(insert(versions).values(version=SCHEMA_VERSION))
            elif found != [SCHEMA_VERSION]:raise ValueError('Unsupported database schema; run an approved migration before startup.')
        if self.engine.dialect.name == 'sqlite' and self.engine.url.database not in {None, '', ':memory:'}:
            os.chmod(self.engine.url.database, 0o600)

    def _bronze_triggers(self):
        """Bronze rows are append-only: the database itself rejects UPDATE and DELETE."""
        message = 'bronze_registry is immutable'
        with self.engine.begin() as conn:
            if self.engine.dialect.name == 'sqlite':
                for action in ('UPDATE', 'DELETE'):
                    conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS bronze_registry_no_{action.lower()} BEFORE {action} ON bronze_registry BEGIN SELECT RAISE(ABORT, '{message}'); END")
            elif self.engine.dialect.name == 'postgresql':
                schema = os.getenv('DATABASE_SCHEMA', 'warisan')
                conn.exec_driver_sql(f'CREATE OR REPLACE FUNCTION "{schema}".bronze_registry_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION \'{message}\'; END; $$ LANGUAGE plpgsql')
                conn.exec_driver_sql(f'DROP TRIGGER IF EXISTS bronze_registry_immutable ON "{schema}".bronze_registry')
                conn.exec_driver_sql(f'CREATE TRIGGER bronze_registry_immutable BEFORE UPDATE OR DELETE ON "{schema}".bronze_registry FOR EACH ROW EXECUTE FUNCTION "{schema}".bronze_registry_immutable()')

    @staticmethod
    def _register_bronze(conn, batch_id, batch):
        for entry in batch.get('bronze', []):
            found = conn.execute(select(bronze_registry.c.sha256).where(bronze_registry.c.batch_id == batch_id, bronze_registry.c.sha256 == entry['sha256'])).first()
            if not found:
                conn.execute(insert(bronze_registry).values(batch_id=batch_id, sha256=entry['sha256'], filename=entry['filename'], bytes=entry['bytes'], ingested_at=entry['ingested_at'], registered_at=utcnow()))

    def list_bronze(self, batch_id=None):
        query = select(bronze_registry).order_by(bronze_registry.c.registered_at, bronze_registry.c.sha256)
        if batch_id:query = query.where(bronze_registry.c.batch_id == batch_id)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query).mappings()]

    @contextmanager
    def write(self):
        with self.engine.connect() as conn:
            if self.engine.dialect.name == 'sqlite':conn.exec_driver_sql('BEGIN IMMEDIATE')
            else:conn.begin()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def close(self):
        self.engine.dispose()

    @staticmethod
    def _event(conn, kind, payload):
        event_id = uuid.uuid4().hex
        conn.execute(insert(outbox).values(id=event_id, event_type=kind, payload={'event_id': event_id, 'event': kind, 'timestamp': utcnow(), **payload}, status='pending', attempts=0, next_attempt=0, created_at=utcnow()))

    def _store_rows(self, conn, batch_id, batch):
        existing = {row['local_id']: row for row in conn.execute(select(records).where(records.c.batch_id == batch_id)).mappings()}
        for record in batch['records']:
            previous = existing.pop(record['id'], None)
            if previous and digest(previous['data']) == digest(record):continue
            values = dict(batch_id=batch_id, local_id=record['id'], status=record['status'], data=record, revision=previous['revision'] + 1 if previous else 1)
            if previous:conn.execute(update(records).where(records.c.id == previous['id']).values(**values))
            else:conn.execute(insert(records).values(id=f'{batch_id}:{record["id"]}', **values))
        if existing:
            raise ValueError('Persisted records cannot be silently removed; start a new batch instead.')

    def create_batch(self, batch, context):
        batch, context = safe_batch(batch), safe_context(context)
        batch_id = uuid.uuid4().hex
        with self.write() as conn:
            conn.execute(insert(batches).values(id=batch_id, revision=1, snapshot=batch, context=context, created_at=utcnow(), updated_at=utcnow()))
            self._store_rows(conn, batch_id, batch)
            self._register_bronze(conn, batch_id, batch)
            self._event(conn, 'batch.created', {'batch_id': batch_id, 'records': len(batch['records'])})
        return batch_id, 1

    def load_batch(self, batch_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(batches).where(batches.c.id == batch_id)).mappings().first()
            if not row:raise KeyError('Batch not found')
            return copy.deepcopy(dict(row))

    def list_batches(self):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(batches.c.id, batches.c.revision, batches.c.created_at, batches.c.updated_at).order_by(batches.c.updated_at.desc())).mappings()]

    def save_batch(self, batch_id, batch, context, expected_revision):
        batch, context = safe_batch(batch), safe_context(context)
        with self.write() as conn:
            previous = conn.execute(select(batches).where(batches.c.id == batch_id).with_for_update()).mappings().first()
            if not previous:raise KeyError('Batch not found')
            if previous['revision'] != expected_revision:raise ConflictError('Batch changed externally. Sync and retry; local changes were not saved.')
            if digest(previous['snapshot']) == digest(batch) and digest(previous['context']) == digest(context):return expected_revision
            revision = expected_revision + 1
            conn.execute(update(batches).where(batches.c.id == batch_id).values(snapshot=batch, context=context, revision=revision, updated_at=utcnow()))
            self._store_rows(conn, batch_id, batch)
            self._register_bronze(conn, batch_id, batch)
            self._event(conn, 'batch.updated', {'batch_id': batch_id, **self._progress(batch,context)})
        return revision

    @staticmethod
    def _progress(batch, context=None):
        rows = batch['records']
        queued = [r for r in rows if r['status'] == 'PTSL_REVIEW' or any(h.get('action') in {'APPROVE', 'QUARANTINE', 'UNSURE'} for h in r.get('history', []))]
        remaining = sum(r['status'] == 'PTSL_REVIEW' for r in rows)
        import watan_gold_studio as w
        current_profile = w.profile_hash(w.QwenConfig(**{k:v for k,v in context['config'].items() if k in {'base_url','model','mode','timeout','max_tokens','target'}}),context['policy']) if context else None
        pending = sum(r['status'] in {'PENDING', 'ERROR'} or bool(current_profile and r.get('verification') and r['verification']['profile_sha256']!=current_profile) for r in rows)
        eligible = sum(r['status'] in {'AUTO_PASS', 'HUMAN_APPROVED'} for r in rows)
        verified = bool(rows) and any(r.get('verification') for r in rows)
        return {'total_queued': len(queued), 'completed': len(queued) - remaining, 'remaining': remaining, 'ready_for_release': verified and not pending and remaining == 0 and eligible > 0}

    def status(self, batch_id=None):
        if batch_id:
            saved=self.load_batch(batch_id)
            return {'batch_id': batch_id, **self._progress(saved['snapshot'],saved['context'])}
        snapshots = [self.load_batch(row['id']) for row in self.list_batches()]
        progress = [self._progress(row['snapshot'],row['context']) for row in snapshots]
        return {**{key: sum(p[key] for p in progress) for key in ('total_queued', 'completed', 'remaining')}, 'ready_for_release': bool(progress) and all(p['ready_for_release'] for p in progress)}

    def queue(self, batch_id=None):
        import watan_gold_studio as w
        statement = select(records).where(records.c.status == 'PTSL_REVIEW').order_by(records.c.id)
        if batch_id:statement = statement.where(records.c.batch_id == batch_id)
        with self.engine.connect() as conn:
            result = []
            for row in conn.execute(statement).mappings():
                record = row['data']
                # Strict masking here, including explicitly allowed dummy fixture values.
                dialogue = record.get('kind') == 'dialogue'
                original = w.canonical_json(record['original']) if dialogue else record.get('original_text', '')
                proposed = w.canonical_json(record['candidate']) if dialogue else record.get('text', '')
                meta = record.get('meta', {})
                result.append({'record_id': row['id'], 'batch_id': row['batch_id'], 'source_record_id': row['local_id'], 'expected_revision': row['revision'], 'kind': record['kind'], 'masked_text': w.mask_pii(original)[0], 'qwen_suggestion': w.mask_pii(proposed)[0], 'reason': w.mask_pii(record.get('reason', ''))[0], 'stage': record['stage'], 'metadata': {k: w.mask_pii(str(meta[k]))[0] for k in ('doc_title', 'section', 'source_id', 'script', 'lang', 'language', 'origin') if k in meta}})
            return result

    def submit(self, verdicts, allowed_roles=('Linguist',)):
        """Atomically apply all decisions through the existing backend role/PII gate."""
        import watan_gold_studio as w
        if not isinstance(verdicts, list) or not 1 <= len(verdicts) <= 100:raise ValueError('Submit between 1 and 100 verdicts.')
        ids = [v['record_id'] for v in verdicts]
        if len(set(ids)) != len(ids):raise ValueError('Repeated record IDs in one submission.')
        batch_ids = sorted({rid.split(':', 1)[0] for rid in ids})
        with self.write() as conn:
            loaded = {}
            for batch_id in batch_ids:
                row = conn.execute(select(batches).where(batches.c.id == batch_id).with_for_update()).mappings().first()
                if not row:raise KeyError('Batch not found')
                loaded[batch_id] = dict(row, snapshot=copy.deepcopy(row['snapshot']))
            for verdict in verdicts:
                row = conn.execute(select(records).where(records.c.id == verdict['record_id'])).mappings().first()
                if not row:raise KeyError('Record not found')
                if row['revision'] != verdict.get('expected_revision'):raise ConflictError('Task changed. Fetch a fresh queue before resubmitting.')
                batch = loaded[row['batch_id']]
                record = next(r for r in batch['snapshot']['records'] if r['id'] == row['local_id'])
                role = w.review_role(verdict['reviewer_role'])
                if role not in allowed_roles:raise PermissionError('This API credential does not grant the submitted reviewer role.')
                config = w.QwenConfig(**{k:v for k,v in batch['context']['config'].items() if k in {'base_url','model','mode','timeout','max_tokens','target'}})
                profile = w.profile_hash(config, batch['context']['policy'])
                action = {'Good':'APPROVE','Bad':'QUARANTINE','Unsure':'UNSURE'}.get(verdict['verdict'])
                if not action:raise ValueError('Verdict must be Good, Bad or Unsure.')
                edited = verdict.get('edited_text')
                if edited is not None and action != 'APPROVE':raise ValueError('Edits are only accepted with Good.')
                candidate = None
                if edited is not None:
                    if record['kind'] == 'dialogue':
                        candidate = w.strict_json(edited) if isinstance(edited,str) and edited.lstrip().startswith('[') else copy.deepcopy(record['candidate']) if isinstance(edited,str) else edited
                        if isinstance(edited,str) and not edited.lstrip().startswith('['):candidate[-1]['content']=w.normalize_text(edited)
                        if not isinstance(candidate,list):raise ValueError('Dialogue edited_text must be a complete messages array.')
                    else:
                        if not isinstance(edited,str):raise ValueError('Prose edited_text must be a string.')
                        candidate = w.normalize_text(edited)
                w.human_decision(record, action, role, profile, candidate=candidate)
                record['history'][-1].update(channel='PTSL_API', verdict=verdict['verdict'], reviewer_notes=w.mask_pii(verdict.get('reviewer_notes') or '')[0])
            for batch_id,batch in loaded.items():
                snapshot = safe_batch(batch['snapshot'])
                conn.execute(update(batches).where(batches.c.id==batch_id).values(snapshot=snapshot, revision=batch['revision']+1, updated_at=utcnow()))
                self._store_rows(conn,batch_id,snapshot)
                self._event(conn,'ptsl.reviewed',{'batch_id':batch_id,**self._progress(snapshot,batch['context'])})
        return {'accepted':len(verdicts),'batches':{batch_id:self.status(batch_id) for batch_id in batch_ids}}

    def save_release(self, batch_id, files, expected_revision=None):
        import watan_gold_studio as w
        manifest = json.loads(files['manifest.json'])
        version, rid = manifest['gold_version'], manifest['release_id']
        w.valid_gold_version(version)
        if not re.fullmatch(r'wr-[a-f0-9]{20}',rid):raise ValueError('Invalid release ID')
        key=f'{version}/{rid}'
        archive=w.bundle_zip(files)
        with self.write() as conn:
            current=conn.execute(select(batches.c.revision).where(batches.c.id==batch_id).with_for_update()).scalar_one()
            if expected_revision is not None and current!=expected_revision:raise ConflictError('Batch changed during release validation. Sync and rebuild.')
            existing=conn.execute(select(releases).where(releases.c.key==key)).mappings().first()
            if existing:
                if existing['manifest']['files'] != manifest['files']:raise ConflictError('Release ID collision; immutable release cannot be replaced.')
            else:
                conn.execute(insert(releases).values(key=key,batch_id=batch_id,gold_version=version,release_id=rid,created_at=utcnow(),manifest=manifest,archive=archive,sha256=digest(archive)))
                self._event(conn,'release.created',{'batch_id':batch_id,'gold_version':version,'release_id':rid})
        restored=self.release_files(key)
        self.materialize_release(key,restored)
        return restored

    def list_releases(self, batch_id=None):
        statement=select(releases.c.key,releases.c.batch_id,releases.c.gold_version,releases.c.release_id,releases.c.created_at,releases.c.sha256).order_by(releases.c.created_at.desc())
        if batch_id:statement=statement.where(releases.c.batch_id==batch_id)
        with self.engine.connect() as conn:return [dict(r) for r in conn.execute(statement).mappings()]

    def release_files(self,key):
        with self.engine.connect() as conn:
            row=conn.execute(select(releases).where(releases.c.key==key)).mappings().first()
            if not row:raise KeyError('Release not found')
            if digest(row['archive']) != row['sha256']:raise ValueError('Stored release checksum mismatch')
            with zipfile.ZipFile(io.BytesIO(row['archive'])) as archive:
                files={name:archive.read(name) for name in archive.namelist()}
        for line in files['checksums.sha256'].decode().splitlines():
            checksum,name=line.split('  ',1)
            if name not in files or digest(files[name]) != checksum:raise ValueError('Release artifact checksum mismatch')
        return files

    def materialize_release(self,key,files):
        self.release_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        folder=(self.release_root/key).resolve()
        if not folder.is_relative_to(self.release_root):raise ValueError('Unsafe release path')
        for name in files:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('Unsafe artifact filename')
        if folder.exists():
            # Restore missing/corrupted mirrors from the authoritative DB snapshot.
            for name,data in files.items():
                if not (folder/name).exists() or digest((folder/name).read_bytes())!=digest(data):
                    with tempfile.NamedTemporaryFile(dir=folder,delete=False) as tmp:
                        tmp.write(data);temp=Path(tmp.name)
                    os.replace(temp,folder/name)
            return folder
        folder.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with tempfile.TemporaryDirectory(dir=folder.parent) as temp:
            for name,data in files.items():
                path=Path(temp)/name;path.write_bytes(data);path.chmod(0o600)
            try:os.rename(temp,folder)
            except FileExistsError:pass
        return folder

    def claim_webhook(self):
        now=int(time.time());lease=uuid.uuid4().hex
        with self.write() as conn:
            row=conn.execute(select(outbox).where(outbox.c.status.in_(['pending','sending']),outbox.c.next_attempt<=now).order_by(outbox.c.created_at).with_for_update(skip_locked=True)).mappings().first()
            if not row:return None
            conn.execute(update(outbox).where(outbox.c.id==row['id']).values(status='sending',lease=lease,next_attempt=now+60))
            return dict(row,lease=lease)

    def finish_webhook(self,event_id,lease,success):
        with self.write() as conn:
            row=conn.execute(select(outbox).where(outbox.c.id==event_id,outbox.c.lease==lease)).mappings().first()
            if not row:return
            attempts=row['attempts']+1
            conn.execute(update(outbox).where(outbox.c.id==event_id,outbox.c.lease==lease).values(status='delivered' if success else 'failed' if attempts>=8 else 'pending',attempts=attempts,lease=None,next_attempt=int(time.time())+min(3600,2**attempts*5)))
