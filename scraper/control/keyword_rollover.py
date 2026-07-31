#!/usr/bin/env python3
"""Generate and activate the next keyword batch after the current campaign drains."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg2
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "web"))

from app.keyword_generator import GenerationError, KeywordGenerator  # noqa: E402

LOCK_ID = 2_026_072_024


class SecretValue:
    def __init__(self, value: str):
        self.value = value

    def get_secret_value(self) -> str:
        return self.value


class GeneratorSettings:
    def __init__(self):
        self.openrouter_api_key = SecretValue(os.environ.get("OPENROUTER_API_KEY", ""))
        self.openrouter_model = os.environ.get(
            "OPENROUTER_MODEL", "deepseek/deepseek-v4-flash",
        )
        self.openrouter_base_url = os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1",
        )
        self.openrouter_timeout_secs = float(os.environ.get("OPENROUTER_TIMEOUT_SECS", "45"))


def load_keywords(path: Path) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        keyword = line.strip()
        key = keyword.casefold()
        if keyword and not keyword.startswith("#") and key not in seen:
            seen.add(key)
            keywords.append(keyword)
    return keywords


def load_states(path: Path) -> list[str]:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(state).lower() for state in config.get("states", [])]


def batch_started_on(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()


def campaign_complete(connection, keywords: list[str], states: list[str], started_on: date) -> bool:
    expected = len(keywords) * len(states)
    if not expected:
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT count(*) FROM keyword_history
               WHERE lower(keyword)=ANY(%s) AND lower(state)=ANY(%s)
                 AND last_enqueued >= %s""",
            ([value.casefold() for value in keywords], states, started_on),
        )
        completed_pairs = int(cursor.fetchone()[0])
        cursor.execute(
            """SELECT count(*) FROM river_job
               WHERE kind='scrape'
                 AND state IN ('available','pending','scheduled','retryable','running')
                 AND lower(args->>'keyword')=ANY(%s)
                 AND lower(args->>'state')=ANY(%s)""",
            ([value.casefold() for value in keywords], states),
        )
        active_jobs = int(cursor.fetchone()[0])
    return completed_pairs >= expected and active_jobs == 0


def used_keywords(connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT keyword FROM (
                 SELECT trim(keyword) AS keyword FROM enqueue_log
                 UNION SELECT trim(keyword) FROM keyword_history
                 UNION SELECT trim(keyword) FROM businesses
               ) used WHERE COALESCE(keyword, '') <> ''"""
        )
        return {str(row[0]) for row in cursor.fetchall()}


def rollover_enabled(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT lower(value) IN ('1','true','yes','on') FROM app_config "
            "WHERE key='auto_keyword_rollover'"
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def error_in_cooldown(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT EXISTS (
                 SELECT 1 FROM keyword_rollover_events
                 WHERE status='error' AND created_at >= NOW()-interval '15 minutes'
                 ORDER BY created_at DESC LIMIT 1
               )"""
        )
        return bool(cursor.fetchone()[0])


def write_keywords(path: Path, keywords: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}"))
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(keywords) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_error(connection, message: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO keyword_rollover_events (status,message) VALUES ('error',%s)",
            (message[:300],),
        )
    connection.commit()


def restore_keywords(path: Path, snapshot: bytes | None) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.restore")
    try:
        if snapshot is None:
            path.unlink(missing_ok=True)
            return
        with temporary.open("wb") as handle:
            handle.write(snapshot)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def activate_generation(
    connection,
    keywords_path: Path,
    current: list[str],
    result,
    generation_id,
    model: str,
) -> None:
    snapshot = (
        keywords_path.read_bytes()
        if keywords_path.exists()
        else None
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO keyword_generations
                     (id,mode,model,keywords,excluded_count)
                     VALUES (%s,'auto',%s,%s::jsonb,%s)""",
                (
                    str(generation_id),
                    model,
                    json.dumps(result.keywords),
                    result.excluded_count,
                ),
            )
            cursor.execute(
                "UPDATE keyword_generations SET accepted_at=NOW() WHERE id=%s",
                (str(generation_id),),
            )
            cursor.execute(
                """INSERT INTO keyword_rollover_events
                     (status,generation_id,previous_keywords,next_keywords,message)
                     VALUES ('generated',%s,%s::jsonb,%s::jsonb,%s)""",
                (
                    str(generation_id),
                    json.dumps(current),
                    json.dumps(result.keywords),
                    "Activated 25 automatically generated keywords",
                ),
            )
            cursor.execute(
                "DELETE FROM keyword_generations "
                "WHERE created_at < NOW()-interval '90 days'"
            )
        write_keywords(keywords_path, result.keywords)
        connection.commit()
    except Exception:
        connection.rollback()
        restore_keywords(keywords_path, snapshot)
        raise


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    keywords_path = Path(os.environ.get(
        "KEYWORDS_PATH", str(HERE / "runtime" / "keywords.txt"),
    ))
    states_path = Path(os.environ.get("ACTIVE_STATES_PATH", str(HERE / "active_states.yaml")))
    pause_path = Path(os.environ.get(
        "GMS_PIPELINE_PAUSE_FILE", str(HERE / "runtime" / "pipeline.paused"),
    ))
    if not dsn:
        print("auto-keywords: DATABASE_URL is not configured", file=sys.stderr)
        return 1

    connection = psycopg2.connect(dsn)
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
            locked = bool(cursor.fetchone()[0])
        if not locked:
            print("auto-keywords: another rollover check is active")
            return 0
        if not rollover_enabled(connection):
            print("auto-keywords: disabled")
            return 0
        if pause_path.exists():
            print("auto-keywords: pipeline paused; rollover deferred")
            return 0
        if error_in_cooldown(connection):
            print("auto-keywords: provider retry cooldown active")
            return 0

        current = load_keywords(keywords_path)
        states = load_states(states_path)
        if not campaign_complete(connection, current, states, batch_started_on(keywords_path)):
            print("auto-keywords: current batch is still active")
            return 0

        settings = GeneratorSettings()
        if not settings.openrouter_api_key.get_secret_value().strip():
            record_error(connection, "OpenRouter is not configured for automatic rollover")
            return 0
        excluded = used_keywords(connection) | set(current)
        try:
            result = asyncio.run(KeywordGenerator(settings).generate("broad", excluded))
        except GenerationError as error:
            record_error(connection, str(error))
            print(f"auto-keywords: {error}", file=sys.stderr)
            return 0

        if load_keywords(keywords_path) != current:
            print("auto-keywords: active list changed during generation; draft discarded")
            return 0

        generation_id = uuid4()
        activate_generation(
            connection=connection,
            keywords_path=keywords_path,
            current=current,
            result=result,
            generation_id=generation_id,
            model=settings.openrouter_model,
        )
        print(f"auto-keywords: activated generation {generation_id} with 25 keywords")
        return 0
    except Exception as error:
        connection.rollback()
        if rollover_enabled(connection) and not error_in_cooldown(connection):
            record_error(connection, "Automatic rollover failed; inspect the service log")
        print(f"auto-keywords: unexpected {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
