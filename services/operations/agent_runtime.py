"""Durable, bounded local agent task runtime.

Agents here are concrete sandboxed local capabilities, *not* autonomous LLMs.
No arbitrary shell, browser, or network access is exposed. External provider
agents must obtain a separately scoped execution contract before joining.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import os
import sqlite3
import uuid


class AgentRuntimeConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


TASKS = (
    ('requirements', 'requirements_analyst', (), 'inspect_approved_plan'),
    ('builder', 'local_site_builder', ('requirements',), 'write_sandboxed_preview'),
    ('qa', 'artifact_verifier', ('builder',), 'verify_preview'),
)


class LocalAgentRuntime:
    """One-step-at-a-time, resumable local execution over an approved mission.

    All updates to task state are SQLite transactions. Interrupted RUNNING tasks
    become BLOCKED and require explicit operator reconciliation, not blind retry.
    """

    def __init__(self, mission_service):
        self.missions = mission_service
        self.root = (mission_service.root / 'agent_runtime').resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = mission_service.db
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS agent_tasks (
                mission_id TEXT NOT NULL, owner TEXT NOT NULL, task_id TEXT NOT NULL,
                agent TEXT NOT NULL, action TEXT NOT NULL, dependencies TEXT NOT NULL,
                status TEXT NOT NULL, evidence TEXT, error TEXT, updated TEXT NOT NULL,
                PRIMARY KEY (mission_id, task_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS agent_run_controls (
                mission_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                state TEXT NOT NULL, source_hash TEXT NOT NULL, created TEXT NOT NULL)''')

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _mission(self, owner, mission_id):
        if not isinstance(mission_id, str) or not mission_id.startswith('msn_') or len(mission_id) != 36:
            raise KeyError('Mission not found')
        mission = self.missions.get(owner, mission_id)
        if mission['status'] != 'PLAN_CREATED':
            raise AgentRuntimeConflict('Approved mission plan required')
        body = self.missions.artifact(owner, mission_id)
        return mission, sha256(body).hexdigest()

    def status(self, owner, mission_id):
        self.missions.get(owner, mission_id)
        with self._db() as db:
            control = db.execute('SELECT state,source_hash FROM agent_run_controls WHERE mission_id=? AND owner=?',
                                 (mission_id, owner)).fetchone()
            rows = db.execute('''SELECT task_id,agent,action,dependencies,status,evidence,error,updated
                FROM agent_tasks WHERE mission_id=? AND owner=? ORDER BY rowid''', (mission_id, owner)).fetchall()
        return {'mission_id': mission_id, 'state': control['state'] if control else 'NOT_STARTED',
                'execution_mode': 'BOUNDED_LOCAL_AGENTS_NO_LLM_OR_NETWORK',
                'objective_completed': False, 'tasks': [
                    {**dict(r), 'dependencies': json.loads(r['dependencies']),
                     'evidence': json.loads(r['evidence']) if r['evidence'] else None} for r in rows]}

    def start(self, owner, mission_id):
        mission, digest = self._mission(owner, mission_id)
        with self._db() as db:
            if db.execute('SELECT 1 FROM agent_run_controls WHERE mission_id=?', (mission_id,)).fetchone():
                raise AgentRuntimeConflict('Runtime already started')
            db.execute('INSERT INTO agent_run_controls VALUES (?,?,?,?,?)',
                       (mission_id, owner, 'READY', digest, _now()))
            for task_id, agent, deps, action in TASKS:
                db.execute('INSERT INTO agent_tasks VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (mission_id, owner, task_id, agent, action, json.dumps(deps),
                            'PENDING', None, None, _now()))
        return self.status(owner, mission_id)

    def cancel(self, owner, mission_id):
        self.missions.get(owner, mission_id)
        with self._db() as db:
            ctl = db.execute('SELECT state FROM agent_run_controls WHERE mission_id=? AND owner=?',
                             (mission_id, owner)).fetchone()
            if not ctl:
                raise AgentRuntimeConflict('Runtime not started')
            if ctl['state'] in ('COMPLETED', 'CANCELLED'):
                raise AgentRuntimeConflict('Runtime already terminal')
            db.execute("UPDATE agent_run_controls SET state='CANCELLED' WHERE mission_id=? AND owner=?", (mission_id, owner))
            db.execute("UPDATE agent_tasks SET status='CANCELLED',updated=? WHERE mission_id=? AND owner=? AND status='PENDING'",
                       (_now(), mission_id, owner))
        return self.status(owner, mission_id)

    def reconcile(self, owner, mission_id):
        """Fail closed after a crash: uncertain tasks need manual review."""
        self.missions.get(owner, mission_id)
        with self._db() as db:
            rows = db.execute("SELECT task_id FROM agent_tasks WHERE mission_id=? AND owner=? AND status='RUNNING'",
                              (mission_id, owner)).fetchall()
            if rows:
                db.execute("UPDATE agent_tasks SET status='BLOCKED',error='Interrupted; inspect workspace before retry',updated=? WHERE mission_id=? AND owner=? AND status='RUNNING'",
                           (_now(), mission_id, owner))
                db.execute("UPDATE agent_run_controls SET state='BLOCKED' WHERE mission_id=? AND owner=?", (mission_id, owner))
        return self.status(owner, mission_id)

    def _workspace(self, mission_id):
        path = self.root / mission_id
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise AgentRuntimeConflict('Unsafe mission workspace')
        path.mkdir(mode=0o700, exist_ok=True)
        if path.resolve().parent != self.root:
            raise AgentRuntimeConflict('Workspace escapes approved root')
        return path

    @staticmethod
    def _write_once(path, content):
        if path.is_symlink() or path.exists():
            raise AgentRuntimeConflict('Refusing to overwrite existing artifact')
        with path.open('xb') as out:
            os.chmod(path, 0o600)
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        if sha256(path.read_bytes()).digest() != sha256(content).digest():
            raise AgentRuntimeConflict('Artifact write verification failed')
        return {'name': path.name, 'sha256': sha256(content).hexdigest(), 'bytes': len(content)}

    def _execute(self, action, mission, digest):
        path = self._workspace(mission['id'])
        if action == 'inspect_approved_plan':
            assert sha256(self.missions.artifact(mission['owner'], mission['id'])).hexdigest() == digest
            return {'source_plan_sha256': digest, 'checked': True, 'kind': 'SOURCE_INTEGRITY'}
        if action == 'write_sandboxed_preview':
            title = escape(mission['title'], quote=True)
            objective = escape(mission['objective'], quote=True)
            page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<title>' + title + '</title></head><body><main><h1>' + title +
                    '</h1><p>' + objective + '</p><small>HOOD local prototype. Not a completed business deliverable.</small>'
                    '</main></body></html>').encode('utf-8')
            evidence = self._write_once(path / 'preview.html', page)
            evidence['kind'] = 'SANDBOXED_HTML_PREVIEW'
            evidence['objective_completed'] = False
            return evidence
        if action == 'verify_preview':
            with self._db() as db:
                row = db.execute("SELECT evidence FROM agent_tasks WHERE mission_id=? AND task_id='builder' AND status='COMPLETED'",
                                 (mission['id'],)).fetchone()
            if not row:
                raise AgentRuntimeConflict('Builder evidence unavailable')
            evidence = json.loads(row['evidence'])
            target = path / 'preview.html'
            if target.is_symlink() or not target.is_file():
                raise AgentRuntimeConflict('Missing or unsafe preview')
            actual = target.read_bytes()
            if sha256(actual).hexdigest() != evidence['sha256'] or len(actual) != evidence['bytes']:
                raise AgentRuntimeConflict('Preview integrity mismatch')
            if not actual.startswith(b'<!doctype html>'):
                raise AgentRuntimeConflict('Invalid HTML preview')
            return {'kind': 'ARTIFACT_HASH_VERIFICATION', 'sha256': evidence['sha256'],
                    'checked': True, 'not_browser_or_business_verified': True}
        raise AgentRuntimeConflict('Unknown or unauthorized agent action')

    def preview(self, owner, mission_id):
        """Return approved local output only after actual QA and hash verification."""
        result = self.status(owner, mission_id)
        if result['state'] != 'COMPLETED':
            raise AgentRuntimeConflict('Verified preview is not available')
        tasks = {item['task_id']: item for item in result['tasks']}
        if tasks['qa']['status'] != 'COMPLETED' or not tasks['qa']['evidence']['checked']:
            raise AgentRuntimeConflict('QA evidence missing')
        path = self._workspace(mission_id) / 'preview.html'
        if path.is_symlink() or not path.is_file():
            raise AgentRuntimeConflict('Invalid preview path')
        content = path.read_bytes()
        receipt = tasks['builder']['evidence']
        if sha256(content).hexdigest() != receipt['sha256'] or len(content) != receipt['bytes']:
            raise AgentRuntimeConflict('Preview content changed since QA')
        return content

    def advance(self, owner, mission_id):
        """Execute exactly one predetermined, locally confined task per call."""
        mission, digest = self._mission(owner, mission_id)
        mission['owner'] = owner
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            ctl = db.execute('SELECT state,source_hash FROM agent_run_controls WHERE mission_id=? AND owner=?',
                             (mission_id, owner)).fetchone()
            if not ctl or ctl['state'] not in ('READY', 'RUNNING'):
                raise AgentRuntimeConflict('Runtime not started or not executable')
            if ctl['source_hash'] != digest:
                raise AgentRuntimeConflict('Approved input changed')
            rows = db.execute('SELECT * FROM agent_tasks WHERE mission_id=? AND owner=? ORDER BY rowid',
                              (mission_id, owner)).fetchall()
            if any(r['status'] in ('FAILED', 'BLOCKED', 'RUNNING') for r in rows):
                raise AgentRuntimeConflict('Failed or interrupted task requires reconciliation')
            selected = next((r for r in rows if r['status'] == 'PENDING'), None)
            if not selected:
                raise AgentRuntimeConflict('No pending tasks')
            complete = {r['task_id'] for r in rows if r['status'] == 'COMPLETED'}
            if not set(json.loads(selected['dependencies'])).issubset(complete):
                raise AgentRuntimeConflict('Dependencies not complete')
            db.execute("UPDATE agent_tasks SET status='RUNNING',updated=? WHERE mission_id=? AND task_id=?",
                       (_now(), mission_id, selected['task_id']))
            db.execute("UPDATE agent_run_controls SET state='RUNNING' WHERE mission_id=?", (mission_id,))
            db.commit()
        try:
            evidence = self._execute(selected['action'], mission, digest)
            if not isinstance(evidence, dict) or not evidence.get('kind'):
                raise AgentRuntimeConflict('Missing execution evidence')
        except Exception as exc:
            with self._db() as db:
                db.execute("UPDATE agent_tasks SET status='FAILED',error=?,updated=? WHERE mission_id=? AND task_id=? AND status='RUNNING'",
                           (str(exc)[:300], _now(), mission_id, selected['task_id']))
                db.execute("UPDATE agent_run_controls SET state='BLOCKED' WHERE mission_id=?", (mission_id,))
            raise
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            control = db.execute('SELECT state FROM agent_run_controls WHERE mission_id=? AND owner=?', (mission_id, owner)).fetchone()
            if not control or control['state'] != 'RUNNING':
                raise AgentRuntimeConflict('Execution cancelled or interrupted before completion')
            changed = db.execute("UPDATE agent_tasks SET status='COMPLETED',evidence=?,updated=? WHERE mission_id=? AND task_id=? AND status='RUNNING'",
                                 (json.dumps(evidence, sort_keys=True), _now(), mission_id, selected['task_id'])).rowcount
            if changed != 1:
                raise AgentRuntimeConflict('Task changed during execution')
            still = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE mission_id=? AND status='PENDING'", (mission_id,)).fetchone()[0]
            if not still:
                db.execute("UPDATE agent_run_controls SET state='COMPLETED' WHERE mission_id=? AND owner=?", (mission_id, owner))
        return self.status(owner, mission_id)
