from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from jawnix.maintenance import expire_batch_files
from jawnix.models import Agent, BatchArtifact

from conftest import make_request


def _artifact(session, request, *, path, created_at) -> BatchArtifact:
    artifact = BatchArtifact(
        request_id=request.id,
        path=str(path),
        filename=path.name,
        row_count=1,
        byte_count=path.stat().st_size if path.is_file() else 0,
        sha256="a" * 64,
        created_at=created_at,
    )
    session.add(artifact)
    session.flush()
    return artifact


def test_expire_batch_files_deletes_only_aged_files_under_batch_dir(
    session,
    settings,
):
    agent = Agent(slug="maintenance-agent", name="Maintenance Agent")
    session.add(agent)
    session.flush()
    aged_request = make_request(session, agent, 1)
    fresh_request = make_request(session, agent, 1)
    outside_request = make_request(session, agent, 1)

    aged_cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.batch_retention_days + 1
    )
    fresh_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    settings.batch_dir.mkdir(parents=True, exist_ok=True)
    aged_path = settings.batch_dir / "aged.zip"
    aged_path.write_bytes(b"aged artifact bytes")
    fresh_path = settings.batch_dir / "fresh.zip"
    fresh_path.write_bytes(b"fresh artifact bytes")
    outside_dir = settings.batch_dir.parent / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_path = outside_dir / "outside.zip"
    outside_path.write_bytes(b"outside artifact bytes")

    aged_artifact = _artifact(
        session, aged_request, path=aged_path, created_at=aged_cutoff
    )
    fresh_artifact = _artifact(
        session, fresh_request, path=fresh_path, created_at=fresh_cutoff
    )
    outside_artifact = _artifact(
        session,
        outside_request,
        path=outside_path,
        created_at=aged_cutoff,
    )
    session.commit()

    result = expire_batch_files(session, settings)

    assert result == {
        "removed": 1,
        "alreadyMissing": 0,
        "outsideBatchDir": 1,
    }
    assert not aged_path.exists()
    assert fresh_path.exists()
    assert outside_path.exists()

    # Metadata and checksums are retained even though the file is gone: the
    # row is a durable record of delivery, not just a pointer to the CSV.
    for artifact in (aged_artifact, fresh_artifact, outside_artifact):
        session.refresh(artifact)
    assert aged_artifact.sha256 == "a" * 64
    assert aged_artifact.row_count == 1
    assert aged_artifact.path == str(aged_path)
    assert session.scalar(
        select(BatchArtifact).where(BatchArtifact.id == aged_artifact.id)
    ) is not None


def test_expire_batch_files_counts_an_already_missing_file(
    session,
    settings,
):
    agent = Agent(slug="maintenance-missing", name="Maintenance Missing")
    session.add(agent)
    session.flush()
    request = make_request(session, agent, 1)
    settings.batch_dir.mkdir(parents=True, exist_ok=True)
    missing_path = settings.batch_dir / "already-gone.zip"
    _artifact(
        session,
        request,
        path=missing_path,
        created_at=datetime.now(timezone.utc)
        - timedelta(days=settings.batch_retention_days + 1),
    )
    session.commit()

    result = expire_batch_files(session, settings)

    assert result == {
        "removed": 0,
        "alreadyMissing": 1,
        "outsideBatchDir": 0,
    }
