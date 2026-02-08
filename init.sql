-- Multi-Agent Service: PostgreSQL Extension Initialization
-- Runs automatically on first container start via docker-compose volume mount.

CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector: vector type support
CREATE EXTENSION IF NOT EXISTS vectorscale;   -- pgvectorscale: DiskANN indexes (11x faster than HNSW)
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- UUID generation (gen_random_uuid) + credential encryption
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- Trigram-based text search for fuzzy matching
