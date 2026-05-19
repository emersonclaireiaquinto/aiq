-- =============================================================================
-- AI-Q Blueprint - Database Initialization (idempotent — safe to re-run)
-- =============================================================================
--
-- What this script handles:
--   - Creating databases (aiq_checkpoints)
--   - Granting permissions
--   - Creating NAT JobStore table (job_info)
--   - Creating performance indices
--
-- What the app handles automatically:
--   - job_events table (event_store.py creates via SQLAlchemy)
--   - LangGraph checkpoint tables (AsyncPostgresSaver creates them)
--   - summaries table (summary_store.py creates if not exists)
--
-- =============================================================================

-- Create checkpoints database if it doesn't exist
SELECT 'CREATE DATABASE aiq_checkpoints' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'aiq_checkpoints')\gexec

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE aiq_jobs TO aiq;
GRANT ALL PRIVILEGES ON DATABASE aiq_checkpoints TO aiq;

-- =============================================================================
-- Create job store table in aiq_jobs database
-- Note: job_events table is created by the app (event_store.py)
-- =============================================================================
\connect aiq_jobs

-- Job metadata table (NAT JobStore - not auto-created by NAT)
CREATE TABLE IF NOT EXISTS job_info (
    job_id VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL,
    config_file VARCHAR,
    error VARCHAR,
    output_path VARCHAR,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE,
    expiry_seconds INTEGER,
    output VARCHAR,
    is_expired BOOLEAN DEFAULT FALSE
);

-- Performance indices
CREATE INDEX IF NOT EXISTS idx_job_info_status ON job_info(status);
CREATE INDEX IF NOT EXISTS idx_job_info_created_at ON job_info(created_at);

-- =============================================================================
-- Session persistence tables (conversations, messages, report versions)
-- Note: Also auto-created by the app (sessions/models.py) if missing
-- =============================================================================

CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    title VARCHAR(512) NOT NULL DEFAULT 'New Session',
    enabled_data_source_ids JSONB DEFAULT '[]',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    message_type VARCHAR(32),
    metadata_ JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS report_versions (
    version_id VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    parent_version_id VARCHAR(64),
    content TEXT NOT NULL,
    triggering_query TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_report_versions_conversation ON report_versions(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS session_collections (
    session_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_session_collections_user ON session_collections(user_id);
