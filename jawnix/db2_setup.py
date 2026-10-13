"""Provision DB2 and its read-only inventory link from DB1."""
from __future__ import annotations

import logging

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from .config import get_settings
from .job_wakeup import postgres_dsn

DB2_NAME = "jawnix_db2"
SERVER_NAME = "jawnix_db2_inventory"

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
 id uuid PRIMARY KEY, manager_id uuid NOT NULL, state varchar(2) NOT NULL,
 sha256 varchar(64) NOT NULL, status text NOT NULL DEFAULT 'processing',
 processed_rows bigint NOT NULL DEFAULT 0, valid_rows bigint NOT NULL DEFAULT 0,
 rejected_rows bigint NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS inventory_rows (
 upload_id uuid NOT NULL REFERENCES uploads(id) ON DELETE RESTRICT,
 lead_id bigint NOT NULL, phone varchar(10) NOT NULL, title text NOT NULL,
 state varchar(2) NOT NULL, row_number bigint NOT NULL,
 PRIMARY KEY (upload_id, lead_id), UNIQUE (upload_id, phone)
);
CREATE OR REPLACE VIEW published_inventory AS
 SELECT r.lead_id, r.upload_id, r.phone, r.title, r.state, r.row_number, u.manager_id AS owner_id
 FROM inventory_rows r JOIN uploads u ON u.id=r.upload_id WHERE u.status='ready';
CREATE OR REPLACE FUNCTION protect_csv_inventory() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (SELECT 1 FROM uploads WHERE id=CASE WHEN TG_OP='DELETE' THEN OLD.upload_id ELSE NEW.upload_id END AND status='ready')
    OR (TG_OP='UPDATE' AND EXISTS (SELECT 1 FROM uploads WHERE id=OLD.upload_id AND status='ready')) THEN
  RAISE EXCEPTION 'Published CSV rows are immutable';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_csv_inventory ON inventory_rows;
CREATE TRIGGER protect_csv_inventory BEFORE INSERT OR UPDATE OR DELETE ON inventory_rows
 FOR EACH ROW EXECUTE FUNCTION protect_csv_inventory();
CREATE OR REPLACE FUNCTION protect_csv_upload() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status='ready' THEN RAISE EXCEPTION 'Published CSV uploads are immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_csv_upload ON uploads;
CREATE TRIGGER protect_csv_upload BEFORE UPDATE OR DELETE ON uploads
 FOR EACH ROW EXECUTE FUNCTION protect_csv_upload();
"""


def db2_url(settings) -> str:
    if settings.db2_database_url:
        return settings.db2_database_url
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Physical DB2 requires PostgreSQL")
    return url.set(database=DB2_NAME).render_as_string(hide_password=False)


def provision(settings=None) -> None:
    settings = settings or get_settings()
    target = make_url(db2_url(settings))
    if target.get_backend_name() != "postgresql" or not target.database:
        raise ValueError("DB2 requires a PostgreSQL database URL")
    primary = make_url(settings.database_url)
    same_cluster = target.host == primary.host and (target.port or 5432) == (primary.port or 5432)
    if target.database == primary.database and same_cluster:
        raise ValueError("DB2 must be physically separate from DB1")
    # The default is another database in the existing private cluster.
    if not settings.db2_database_url:
        with psycopg.connect(postgres_dsn(settings.database_url), autocommit=True) as db:
            if not db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target.database,)).fetchone():
                db.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(target.database), sql.Identifier(primary.username)))
    with psycopg.connect(postgres_dsn(db2_url(settings))) as source:
        source.execute(SCHEMA)
    with psycopg.connect(postgres_dsn(settings.database_url)) as db:
        fdw_host = "127.0.0.1" if same_cluster else (target.host or "localhost")
        fdw_port = db.execute("SHOW port").fetchone()[0] if same_cluster else str(target.port or 5432)
        db.execute("CREATE EXTENSION IF NOT EXISTS postgres_fdw")
        if not db.execute("SELECT 1 FROM pg_foreign_server WHERE srvname=%s", (SERVER_NAME,)).fetchone():
            db.execute(sql.SQL("CREATE SERVER {} FOREIGN DATA WRAPPER postgres_fdw OPTIONS (host {}, port {}, dbname {}, fetch_size '5000')").format(
                sql.Identifier(SERVER_NAME), sql.Literal(fdw_host), sql.Literal(fdw_port), sql.Literal(target.database)))
        if not db.execute("SELECT 1 FROM pg_user_mappings WHERE srvname=%s AND usename=current_user", (SERVER_NAME,)).fetchone():
            db.execute(sql.SQL("CREATE USER MAPPING FOR CURRENT_USER SERVER {} OPTIONS (user {}, password {})").format(
                sql.Identifier(SERVER_NAME), sql.Literal(target.username), sql.Literal(target.password or "")))
        db.execute(sql.SQL("""CREATE FOREIGN TABLE IF NOT EXISTS db2_inventory_rows (
            lead_id bigint, upload_id uuid, phone varchar(10), title text, state varchar(2),
            row_number bigint, owner_id uuid
        ) SERVER {} OPTIONS (schema_name 'public', table_name 'published_inventory', updatable 'false')""").format(sql.Identifier(SERVER_NAME)))


if __name__ == "__main__":
    try:
        provision()
    except Exception as error:
        # Never put a connection URL or a user mapping password in the log.
        logging.error("DB2 provisioning failed (%s)", type(error).__name__)
        raise SystemExit(1)
    print("Physical DB2 and read-only inventory access are ready.")
