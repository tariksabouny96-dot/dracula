-- =====================================================================
-- HOOD System Database Schema (PostgreSQL + pgvector)
-- Governed by Master System Specification Sections 6, 11, 13 & Appendix B.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 1. Users / Owners
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(100) UNIQUE NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'OWNER',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Projects / Tenants (Tenant Isolation Boundary)
CREATE TABLE IF NOT EXISTS projects (
    project_id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Governed Memory Objects
CREATE TABLE IF NOT EXISTS memories (
    memory_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type VARCHAR(50) NOT NULL, -- WORKING, PERSONAL, PROJECT, SEMANTIC, EPISODIC, PROCEDURAL, DECISION, EXPERIENCE, GOVERNANCE, X_SEALED
    content TEXT NOT NULL,
    project_id VARCHAR(100) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    source VARCHAR(255) NOT NULL,
    source_agent VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ,
    confidence NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    verification_status VARCHAR(50) NOT NULL DEFAULT 'UNVERIFIED',
    sensitivity VARCHAR(50) NOT NULL DEFAULT 'INTERNAL',
    access_policy VARCHAR(50) NOT NULL DEFAULT 'PROJECT_ISOLATED',
    version INT NOT NULL DEFAULT 1,
    supersedes UUID REFERENCES memories(memory_id),
    learning_status VARCHAR(50) NOT NULL DEFAULT 'OBSERVATION',
    embedding vector(1536) -- pgvector semantic embedding
);

CREATE INDEX IF NOT EXISTS idx_memories_project_type ON memories(project_id, type);
CREATE INDEX IF NOT EXISTS idx_memories_temporal ON memories(valid_from, valid_until);

-- 4. Tasks & Task Nodes (DAG Representation)
CREATE TABLE IF NOT EXISTS tasks (
    task_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parent_task_id UUID REFERENCES tasks(task_id),
    title VARCHAR(255) NOT NULL,
    objective TEXT NOT NULL,
    project_id VARCHAR(100) NOT NULL REFERENCES projects(project_id),
    assigned_agent VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    risk_level VARCHAR(10) NOT NULL DEFAULT 'L1',
    dependencies JSONB DEFAULT '[]'::jsonb,
    inputs JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- 5. Approval Requests
CREATE TABLE IF NOT EXISTS approvals (
    approval_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(task_id),
    action_type VARCHAR(100) NOT NULL,
    target VARCHAR(255) NOT NULL,
    risk_level VARCHAR(10) NOT NULL,
    reason TEXT NOT NULL,
    options JSONB DEFAULT '[]'::jsonb,
    recommended_option TEXT,
    checkpoint_ref TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(100),
    rejection_reason TEXT
);

-- 6. Audit & Event Trail (Append-Only)
CREATE TABLE IF NOT EXISTS audit_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor VARCHAR(100) NOT NULL,
    task_id UUID,
    project_id VARCHAR(100) NOT NULL,
    action VARCHAR(100) NOT NULL,
    target VARCHAR(255) NOT NULL,
    policy_decision VARCHAR(50) NOT NULL,
    approval_ref UUID REFERENCES approvals(approval_id),
    input_refs JSONB DEFAULT '[]'::jsonb,
    tool_or_model VARCHAR(100),
    cost NUMERIC(10, 4) DEFAULT 0.0000,
    result TEXT,
    artifact_refs JSONB DEFAULT '[]'::jsonb,
    verification VARCHAR(50) DEFAULT 'PASSED',
    error TEXT,
    correlation_id UUID NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_events(correlation_id);
