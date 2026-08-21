-- Stores normalized news articles returned by a read-only news connector
-- (currently only AlpacaNewsClient; see
-- market_intelligence/data_connectors/alpaca_news.py). This table stores
-- only provider-reported article metadata plus this project's own
-- provenance/ingestion bookkeeping -- never sentiment, impact, direction,
-- confidence, model output, recommendations, option-contract data, orders,
-- credentials, request headers, or raw API responses.
--
-- Idempotency: (provider, provider_article_id) uniquely identifies an
-- article and is the primary key, so re-ingesting the same article never
-- creates a duplicate row.
--
-- Publication/update time is kept strictly separate from retrieval and
-- ingestion bookkeeping time:
--   * created_at / updated_at  -- the provider's reported publication and
--     last-update timestamps for the article itself (nullable, since not
--     every provider response includes them).
--   * retrieved_at             -- when the connector fetched the specific
--     API response that produced the currently-stored values (refreshed on
--     every re-ingestion of an already-known article).
--   * first_ingested_at        -- when this article was first stored in
--     this database; never changed after the initial insert.
--   * last_seen_at             -- when this article was most recently
--     observed by any ingestion run; refreshed on every re-ingestion.
--
-- related_symbols is stored as a sorted, deduplicated list so the
-- on-disk representation is deterministic regardless of the order the
-- provider reported symbols in.
--
-- ingestion_run_id records the ingestion_runs.run_id for the run that most
-- recently wrote this article's stored values (the initial insert, or the
-- most recent refresh), tying every stored article back to an auditable
-- ingestion run. Deliberately not declared as a DuckDB foreign key: a hard
-- FK on ingestion_runs(run_id) would block otherwise-legitimate schema
-- evolution of ingestion_runs (DuckDB refuses to ALTER a table that a FK
-- depends on), so this reference is enforced at the application layer
-- (market_intelligence/storage/news_repository.py) instead.
CREATE TABLE news_articles (
    provider VARCHAR NOT NULL,
    provider_article_id VARCHAR NOT NULL,
    headline VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    article_url VARCHAR NOT NULL,
    summary VARCHAR,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    related_symbols VARCHAR[] NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    first_ingested_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    ingestion_run_id VARCHAR NOT NULL,
    PRIMARY KEY (provider, provider_article_id)
);
