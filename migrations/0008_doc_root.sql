-- Olon migration 0008 — the shared document root (ROADMAP_V2 O1).
-- Docs live in the database, versioned append-only: an edit inserts a new
-- doc_version row and bumps doc.current_version — nothing is ever mutated
-- in place. Every create/update is mirrored to the ledger (doc-created /
-- doc-updated) with its actor, so the doc root has the same public-record
-- properties as the rest of the platform.
-- Visibility: public (default) | private (readable only by the owner agent
-- and the founder — the ROADMAP_V2 §3 IP-protection clause). Making a
-- private doc public is itself a recorded write.
-- Idempotent (CREATE TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS doc (
    id              UUID PRIMARY KEY,
    instance_id     TEXT NOT NULL,
    slug            TEXT NOT NULL,
    title           TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    visibility      TEXT NOT NULL DEFAULT 'public',
    owner_agent_id  UUID REFERENCES agent_registry (agent_id),
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT ix_doc_instance_slug UNIQUE (instance_id, slug)
);

CREATE INDEX IF NOT EXISTS ix_doc_instance ON doc (instance_id);

CREATE TABLE IF NOT EXISTS doc_version (
    id          UUID PRIMARY KEY,
    doc_id      UUID NOT NULL REFERENCES doc (id),
    version     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    change_note TEXT,
    written_by  TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT ix_doc_version_unique UNIQUE (doc_id, version)
);

CREATE INDEX IF NOT EXISTS ix_doc_version_doc ON doc_version (doc_id, version);
