"""4차 DB에 필요한 열만 추가하는 비파괴 업그레이드 (SQLite/PostgreSQL)."""

from sqlalchemy import inspect, text

ADDITIONS = {
    "users": {
        "email_verify_sent_at": "TIMESTAMP",
        "terms_version": "VARCHAR(20)",
        "terms_accepted_at": "TIMESTAMP",
    },
    "games": {
        "product_type": "VARCHAR(30)",
        "catalog_source": "VARCHAR(100)",
        "catalog_rank": "INTEGER",
        "epic_namespace": "VARCHAR(120)",
        "epic_offer_id": "VARCHAR(120)",
        "epic_slug": "VARCHAR(255)",
        "itad_lookup_checked_at": "TIMESTAMP",
        "itad_info_checked_at": "TIMESTAMP",
        "history_synced_at": "TIMESTAMP",
        "history_attempted_at": "TIMESTAMP",
        "history_start_at": "TIMESTAMP",
        "history_error": "TEXT",
        "history_retry_at": "TIMESTAMP",
        "history_queued_at": "TIMESTAMP",
        "history_priority": "INTEGER NOT NULL DEFAULT 0",
        "history_lease_token": "VARCHAR(80)",
        "history_lease_until": "TIMESTAMP",
    },
    "price_snapshots": {
        "source": "VARCHAR(30) NOT NULL DEFAULT 'observed'",
        "is_available": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
    "auto_refresh_state": {"catalog_synced_at": "TIMESTAMP"},
    "crawling_logs": {
        "group_key": "VARCHAR(120)",
        "repeat_count": "INTEGER NOT NULL DEFAULT 1",
        "last_event_at": "TIMESTAMP",
    },
    "software": {
        "source_key": "VARCHAR(80)",
        "kind": "VARCHAR(20) NOT NULL DEFAULT 'software'",
        "logo_url": "TEXT",
    },
    "software_promotions": {
        "source_key": "VARCHAR(120)",
        "currency": "VARCHAR(10) DEFAULT 'KRW'",
        "billing_period": "VARCHAR(60)",
        "eligibility": "TEXT",
        "region": "VARCHAR(120)",
        "renewal_notice": "TEXT",
        "checked_at": "TIMESTAMP",
        "source_status": "VARCHAR(30) DEFAULT 'manual'",
        "last_error": "TEXT",
        "is_offer": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
}


def upgrade_subscription_schema(engine, metadata=None):
    # 테이블/열 이름은 고정 상수만 사용합니다.
    with engine.begin() as connection:
        # reload / multi-worker가 동시에 시작해도 PostgreSQL DDL을 직렬화합니다.
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(5092026)"))
        if metadata is not None:
            metadata.create_all(bind=connection)
        inspector = inspect(connection)
        for table, additions in ADDITIONS.items():
            if not inspector.has_table(table):
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in additions.items():
                if name not in existing:
                    connection.execute(
                        text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
                    )
            if "source_key" in additions:
                connection.execute(
                    text(
                        f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_{table}_source_key" '
                        f'ON "{table}" (source_key)'
                    )
                )
