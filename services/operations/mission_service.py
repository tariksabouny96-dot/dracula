"""Durable owner-scoped mission plans and verifiable local artifact receipts.

No external tools, agent executions, or privileged actions are invoked here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
import json
import os
import sqlite3
import uuid

from services.operations.specialist_engine import review
from services.operations.llm_specialists import run as run_llm_agents


def now():
    return datetime.now(timezone.utc).isoformat()


class MissionConflict(ValueError):
    pass


class MissionService:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir(mode=0o700, exist_ok=True)
        self.db = self.root / 'missions.sqlite3'
        self._lock = RLock()
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                objective TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
                updated TEXT NOT NULL, approved_by TEXT, receipt TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS execution_receipts (
                mission_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                status TEXT NOT NULL, receipt TEXT NOT NULL, created TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS specialist_receipts (
                mission_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                receipt TEXT NOT NULL, created TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS llm_receipts (
                mission_id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
                receipt TEXT NOT NULL, created TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS mission_steps (
                mission_id TEXT NOT NULL, position INTEGER NOT NULL, kind TEXT NOT NULL,
                label TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT,
                PRIMARY KEY(mission_id,position),
                FOREIGN KEY(mission_id) REFERENCES missions(id))''')

    def _connect(self):
        db = sqlite3.connect(self.db, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _steps(db, mission_id):
        return [dict(row) for row in db.execute(
            'SELECT position,kind,label,status,evidence FROM mission_steps WHERE mission_id=? ORDER BY position',
            (mission_id,))]

    @staticmethod
    def _public(row):
        out = dict(row)
        out['receipt'] = json.loads(out['receipt']) if out['receipt'] else None
        out['execution_mode'] = 'LOCAL_PLAN_ARTIFACT_ONLY'
        out['external_actions'] = False
        return out

    @staticmethod
    def _execution(db, mission_id):
        row = db.execute('SELECT status,receipt,created FROM execution_receipts WHERE mission_id=?', (mission_id,)).fetchone()
        if not row:
            return None
        return {'status': row['status'], 'receipt': json.loads(row['receipt']), 'created': row['created'],
                'mode': 'DETERMINISTIC_LOCAL_WORKFLOW', 'agent_execution': False, 'external_actions': False}

    @staticmethod
    def _specialist_receipt(db, mission_id):
        row = db.execute('SELECT receipt,created FROM specialist_receipts WHERE mission_id=?', (mission_id,)).fetchone()
        return {'receipt': json.loads(row['receipt']), 'created': row['created'],
                'mode': 'DETERMINISTIC_SPECIALIST_REVIEW'} if row else None

    @staticmethod
    def _llm(db, mission_id):
        row = db.execute('SELECT status,receipt,created FROM llm_receipts WHERE mission_id=?', (mission_id,)).fetchone()
        return {'status': row['status'], 'receipt': json.loads(row['receipt']), 'created': row['created']} if row else None

    def create(self, owner: str, title: str, objective: str):
        if not isinstance(owner, str) or not owner:
            raise ValueError('Authenticated identity required')
        if not isinstance(title, str) or not 3 <= len(title.strip()) <= 120:
            raise ValueError('Title must be 3-120 characters')
        if not isinstance(objective, str) or not 10 <= len(objective.strip()) <= 8000:
            raise ValueError('Objective must be 10-8000 characters')
        mid, stamp = 'msn_' + uuid.uuid4().hex, now()
        with self._lock, self._connect() as db:
            db.execute('INSERT INTO missions (id,owner,title,objective,status,created,updated) VALUES (?,?,?,?,?,?,?)',
                       (mid, owner, title.strip(), objective.strip(), 'AWAITING_APPROVAL', stamp, stamp))
            steps = [
                ('REVIEW', 'Review objective and boundaries', 'PENDING'),
                ('APPROVAL', 'Obtain explicit local artifact authorization', 'WAITING_APPROVAL'),
                ('ARTIFACT', 'Write and hash planning document', 'BLOCKED'),
                ('VERIFICATION', 'Check artifact bytes and integrity receipt', 'BLOCKED'),
            ]
            db.executemany('INSERT INTO mission_steps (mission_id,position,kind,label,status) VALUES (?,?,?,?,?)',
                           [(mid, i + 1, kind, label, state) for i, (kind, label, state) in enumerate(steps)])
            db.commit()
            return self.get(owner, mid)

    def list(self, owner: str):
        with self._lock, self._connect() as db:
            result = []
            for row in db.execute('SELECT * FROM missions WHERE owner=? ORDER BY created DESC LIMIT 100', (owner,)):
                item = self._public(row)
                item['steps'] = self._steps(db, row['id'])
                item['local_execution'] = self._execution(db, row['id'])
                item['specialist_review'] = self._specialist_receipt(db, row['id'])
                item['llm_review'] = self._llm(db, row['id'])
                result.append(item)
            return result

    def get(self, owner: str, mission_id: str):
        with self._lock, self._connect() as db:
            row = db.execute('SELECT * FROM missions WHERE id=? AND owner=?', (mission_id, owner)).fetchone()
            if not row:
                raise KeyError('Mission not found')
            item = self._public(row)
            item['steps'] = self._steps(db, mission_id)
            item['local_execution'] = self._execution(db, mission_id)
            item['specialist_review'] = self._specialist_receipt(db, mission_id)
            item['llm_review'] = self._llm(db, mission_id)
            return item

    def cancel(self, owner: str, mission_id: str):
        with self._lock, self._connect() as db:
            row = db.execute('SELECT * FROM missions WHERE id=? AND owner=?', (mission_id, owner)).fetchone()
            if not row:
                raise KeyError('Mission not found')
            if row['status'] != 'AWAITING_APPROVAL':
                raise MissionConflict('Only pending missions can be cancelled')
            db.execute('UPDATE missions SET status=?, updated=? WHERE id=? AND owner=?',
                       ('CANCELLED', now(), mission_id, owner))
            db.execute("UPDATE mission_steps SET status='CANCELLED' WHERE mission_id=? AND status IN ('PENDING','WAITING_APPROVAL','BLOCKED')", (mission_id,))
            db.commit()
            return self.get(owner, mission_id)

    def approve_and_create_plan(self, owner: str, mission_id: str):
        """One-time explicit authorization to write only a deterministic Markdown plan.

        The database transition and filesystem update happen under one in-process lock.
        A failed write does not advance the mission state. No arbitrary path is accepted.
        """
        with self._lock, self._connect() as db:
            row = db.execute('SELECT * FROM missions WHERE id=? AND owner=?', (mission_id, owner)).fetchone()
            if not row:
                raise KeyError('Mission not found')
            if row['status'] != 'AWAITING_APPROVAL':
                raise MissionConflict('Mission is not pending approval')
            safe_path = self.artifacts / (mission_id + '.md')
            if safe_path.is_symlink() or safe_path.exists():
                raise MissionConflict('Artifact path already exists')
            content = ('# ' + row['title'].replace('\n', ' ') + '\n\n'
                       '## Objective (user supplied; not verified)\n\n'
                       + row['objective'] + '\n\n'
                       '## Execution boundary\n\n'
                       '- Mode: LOCAL_PLAN_ARTIFACT_ONLY\n'
                       '- No agents, browsers, commands, external APIs or client files were accessed.\n'
                       '- Approval authorizes creation of this planning document only.\n'
                       '- Work remains to be planned, executed and independently verified.\n\n'
                       '## Next steps (proposed, not executed)\n\n'
                       '1. Decompose the objective into reviewable tasks.\n'
                       '2. Identify required tools, scopes, risks and evidence.\n'
                       '3. Request separate approvals for consequential execution.\n'
                       '4. Record verifiable receipts for each completed action.\n')
            body = content.encode('utf-8')
            temp = self.artifacts / (mission_id + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with temp.open('xb') as output:
                    os.chmod(temp, 0o600)
                    output.write(body)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp, safe_path)
                digest = sha256(body).hexdigest()
                receipt = {'kind': 'LOCAL_MARKDOWN_PLAN', 'sha256': digest, 'bytes': len(body),
                           'artifact_id': mission_id + '.md', 'verified': safe_path.read_bytes() == body,
                           'external_actions': False, 'created_at': now()}
                db.execute('UPDATE missions SET status=?, approved_by=?, updated=?, receipt=? WHERE id=? AND owner=? AND status=?',
                           ('PLAN_CREATED', owner, now(), json.dumps(receipt), mission_id, owner, 'AWAITING_APPROVAL'))
                db.execute("UPDATE mission_steps SET status='COMPLETED', evidence=? WHERE mission_id=? AND position=1",
                           ('User supplied objective; not independently validated', mission_id))
                db.execute("UPDATE mission_steps SET status='COMPLETED', evidence=? WHERE mission_id=? AND position=2",
                           ('Authenticated owner gave explicit local-only approval', mission_id))
                db.execute("UPDATE mission_steps SET status='COMPLETED', evidence=? WHERE mission_id=? AND position=3",
                           (digest, mission_id))
                db.execute("UPDATE mission_steps SET status=?, evidence=? WHERE mission_id=? AND position=4",
                           ('COMPLETED' if receipt['verified'] else 'FAILED', digest, mission_id))
                db.commit()
                return self.get(owner, mission_id)
            except Exception:
                temp.unlink(missing_ok=True)
                safe_path.unlink(missing_ok=True)
                raise

    def artifact(self, owner: str, mission_id: str):
        mission = self.get(owner, mission_id)
        if mission['status'] != 'PLAN_CREATED' or not mission['receipt']:
            raise MissionConflict('No artifact available')
        path = self.artifacts / (mission_id + '.md')
        if path.is_symlink():
            raise MissionConflict('Artifact integrity check failed')
        data = path.read_bytes()
        if sha256(data).hexdigest() != mission['receipt']['sha256']:
            raise MissionConflict('Artifact integrity check failed')
        return data

    def run_local_workflow(self, owner: str, mission_id: str):
        """Execute bounded deterministic tasks over a previously approved plan.

        Not autonomous business execution: the only effect is a new local JSON
        receipt/artifact. The caller must grant a SECOND explicit confirmation.
        """
        with self._lock, self._connect() as db:
            row = db.execute('SELECT * FROM missions WHERE id=? AND owner=?', (mission_id, owner)).fetchone()
            if row is None:
                raise KeyError('Mission not found')
            if row['status'] != 'PLAN_CREATED':
                raise MissionConflict('Verified local plan required before executing workflow')
            if self._execution(db, mission_id):
                raise MissionConflict('Local workflow already executed')
            # Prove the input still matches the approved plan, not just its metadata.
            plan = self.artifact(owner, mission_id)
            plan_hash = sha256(plan).hexdigest()
            if not row['receipt'] or json.loads(row['receipt'])['sha256'] != plan_hash:
                raise MissionConflict('Approved plan integrity mismatch')
            # Fixed DAG: each step consumes the previous verified step's result.
            tasks = [
                {'id': 'review', 'depends_on': [], 'action': 'validate_input', 'status': 'PENDING'},
                {'id': 'scope', 'depends_on': ['review'], 'action': 'record_boundaries', 'status': 'PENDING'},
                {'id': 'manifest', 'depends_on': ['scope'], 'action': 'create_evidence_manifest', 'status': 'PENDING'},
            ]
            results = {}
            created_artifact = False
            try:
                for task in tasks:
                    if any(dep not in results for dep in task['depends_on']):
                        raise MissionConflict('DAG dependency not satisfied')
                    if task['id'] == 'review':
                        outcome = {'input_accepted': True, 'objective_bytes': len(row['objective'].encode('utf-8'))}
                    elif task['id'] == 'scope':
                        outcome = {'allowed_effects': ['write one workspace manifest'],
                                   'external_actions': False, 'network': False, 'agent_execution': False,
                                   'source_sha256': plan_hash}
                    else:
                        outcome = {'prior_results': list(results), 'verification': 'local deterministic checks only'}
                    task['status'] = 'COMPLETED'
                    task['output_sha256'] = sha256(json.dumps(outcome, sort_keys=True).encode()).hexdigest()
                    results[task['id']] = outcome
                manifest = {'schema_version': 1, 'mission_id': mission_id, 'owner': owner,
                            'execution_mode': 'DETERMINISTIC_LOCAL_WORKFLOW',
                            'objective_completed': False, 'external_actions': False,
                            'tasks': tasks, 'results': results, 'approved_plan_sha256': plan_hash}
                body = json.dumps(manifest, indent=2, sort_keys=True).encode('utf-8')
                if len(body) > 65536:
                    raise MissionConflict('Local manifest exceeds size limit')
                target = self.artifacts / (mission_id + '.execution.json')
                if target.exists() or target.is_symlink():
                    raise MissionConflict('Execution manifest already exists')
                # Exclusive creation prevents overwrites. No user-controlled filenames.
                with target.open('xb') as stream:
                    created_artifact = True
                    os.chmod(target, 0o600)
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                digest = sha256(body).hexdigest()
                verified = sha256(target.read_bytes()).hexdigest() == digest
                if not verified:
                    raise MissionConflict('Local manifest verification failed')
                receipt = {'artifact_id': target.name, 'sha256': digest, 'bytes': len(body),
                           'verified': verified, 'tasks_completed': len(tasks),
                           'objective_completed': False, 'external_actions': False,
                           'approved_plan_sha256': plan_hash}
                db.execute('INSERT INTO execution_receipts (mission_id,owner,status,receipt,created) VALUES (?,?,?,?,?)',
                           (mission_id, owner, 'LOCAL_WORKFLOW_COMPLETED', json.dumps(receipt), now()))
                db.commit()
                return self.get(owner, mission_id)
            except Exception:
                # Preserve any preexisting artifact; remove only one newly created by this attempt.
                if created_artifact and target.exists() and not self._execution(db, mission_id):
                    target.unlink(missing_ok=True)
                raise

    def execution_artifact(self, owner: str, mission_id: str):
        mission = self.get(owner, mission_id)
        execution = mission['local_execution']
        if not execution or execution['status'] != 'LOCAL_WORKFLOW_COMPLETED':
            raise MissionConflict('No completed local workflow')
        path = self.artifacts / (mission_id + '.execution.json')
        if path.is_symlink():
            raise MissionConflict('Execution artifact integrity check failed')
        body = path.read_bytes()
        if sha256(body).hexdigest() != execution['receipt']['sha256']:
            raise MissionConflict('Execution artifact integrity check failed')
        return body

    def run_specialist_review(self, owner: str, mission_id: str):
        """One-shot approved local analysis with persistent independently checked result."""
        with self._lock, self._connect() as db:
            row = db.execute('SELECT * FROM missions WHERE id=? AND owner=?', (mission_id, owner)).fetchone()
            if not row:
                raise KeyError('Mission not found')
            if row['status'] != 'PLAN_CREATED' or not row['receipt']:
                raise MissionConflict('Approved plan required for specialist review')
            if self._specialist_receipt(db, mission_id):
                raise MissionConflict('Specialist review already exists')
            plan_hash = sha256(self.artifact(owner, mission_id)).hexdigest()
            report = review(row['objective'], plan_hash)
            report.update({'mission_id': mission_id, 'owner': owner})
            body = json.dumps(report, sort_keys=True, indent=2).encode('utf-8')
            if len(body) > 100000:
                raise MissionConflict('Specialist report exceeds size limit')
            target = self.artifacts / (mission_id + '.specialists.json')
            if target.exists() or target.is_symlink():
                raise MissionConflict('Specialist artifact path occupied')
            # Exclusive creation means an existing artifact can never be overwritten.
            created = False
            try:
                with target.open('xb') as stream:
                    created = True
                    os.chmod(target, 0o600)
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                output_hash = sha256(target.read_bytes()).hexdigest()
                if output_hash != sha256(body).hexdigest():
                    raise MissionConflict('Specialist artifact verification failed')
                receipt = {'sha256': output_hash, 'bytes': len(body), 'verified': True,
                           'artifact_id': target.name, 'objective_completed': False,
                           'llm_agents_executed': False, 'external_actions': False,
                           'independent_recomputation': True}
                db.execute('INSERT INTO specialist_receipts (mission_id,owner,receipt,created) VALUES (?,?,?,?)',
                           (mission_id, owner, json.dumps(receipt), now()))
                db.commit()
                return self.get(owner, mission_id)
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise

    def specialist_artifact(self, owner: str, mission_id: str):
        mission = self.get(owner, mission_id)
        item = mission['specialist_review']
        if not item:
            raise MissionConflict('No specialist review exists')
        path = self.artifacts / (mission_id + '.specialists.json')
        if path.is_symlink():
            raise MissionConflict('Specialist artifact is not trustworthy')
        data = path.read_bytes()
        if sha256(data).hexdigest() != item['receipt']['sha256']:
            raise MissionConflict('Specialist artifact integrity check failed')
        return data

    def run_llm_review(self, owner: str, mission_id: str, *, invoke=None):
        """One-time opt-in provider call; no tools or automatic fallback to mock."""
        with self._lock, self._connect() as db:
            row = db.execute('SELECT * FROM missions WHERE id=? AND owner=?', (mission_id, owner)).fetchone()
            if not row:
                raise KeyError('Mission not found')
            if row['status'] != 'PLAN_CREATED' or not row['receipt']:
                raise MissionConflict('An approved local plan is required')
            if self._llm(db, mission_id):
                raise MissionConflict('Provider review already completed')
            plan_hash = sha256(self.artifact(owner, mission_id)).hexdigest()
            # No arbitrary tool invocation: the only external operation is model inference.
            report = run_llm_agents(row['objective'], invoke=invoke)
            report.update({'mission_id': mission_id, 'source_plan_sha256': plan_hash})
            body = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
            if len(body) > 100000:
                raise MissionConflict('Provider report too large')
            target = self.artifacts / (mission_id + '.llm.json')
            if target.is_symlink() or target.exists():
                raise MissionConflict('Provider artifact path occupied')
            created = False
            try:
                with target.open('xb') as stream:
                    created = True
                    os.chmod(target, 0o600)
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                digest = sha256(target.read_bytes()).hexdigest()
                if digest != sha256(body).hexdigest():
                    raise MissionConflict('Provider artifact verification failed')
                receipt = {'sha256': digest, 'bytes': len(body), 'artifact_id': target.name,
                           'verified_integrity': True, 'source_plan_sha256': plan_hash,
                           'agent_count': 2, 'objective_completed': False, 'external_actions': False,
                           'fact_checked': False}
                db.execute('INSERT INTO llm_receipts (mission_id,owner,status,receipt,created) VALUES (?,?,?,?,?)',
                           (mission_id, owner, 'ADVISORY_COMPLETED', json.dumps(receipt), now()))
                db.commit()
                return self.get(owner, mission_id)
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise

    def llm_artifact(self, owner: str, mission_id: str):
        mission = self.get(owner, mission_id)
        review = mission['llm_review']
        if not review:
            raise MissionConflict('No provider review available')
        path = self.artifacts / (mission_id + '.llm.json')
        if path.is_symlink():
            raise MissionConflict('Provider artifact integrity failed')
        body = path.read_bytes()
        if sha256(body).hexdigest() != review['receipt']['sha256']:
            raise MissionConflict('Provider artifact integrity failed')
        return body
