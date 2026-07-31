import re
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.keyword_generator import GenerationError, GenerationResult, KeywordGenerator


@pytest.mark.asyncio
async def test_winners_page_and_generation_draft(app_client, monkeypatch):
    client, settings = app_client
    before = settings.keywords_path.read_text()

    async def fake_generate(self, mode, used_keywords, seed_keyword=None):
        assert "plumbers" in {value.casefold() for value in used_keywords}
        return GenerationResult([f"Unused Service {index}" for index in range(1, 26)], 7)

    monkeypatch.setattr(KeywordGenerator, "generate", fake_generate)
    winners = await client.get("/keywords/winners")
    assert winners.status_code == 200
    assert "Plumbers" in winners.text
    assert "Generate adjacent" in winners.text
    assert "test-openrouter-key" not in winners.text
    assert "Generating adjacent keywords..." in winners.text

    adjacent = await client.post(
        "/keywords/generate",
        data={"mode": "adjacent", "seed_keyword": "plumbers"},
        headers={"HX-Request": "true"},
    )
    assert adjacent.status_code == 204
    assert "draft=" in adjacent.headers["hx-redirect"]

    generated = await client.post(
        "/keywords/generate", data={"mode": "broad"}, headers={"HX-Request": "true"},
    )
    assert generated.status_code == 204
    location = generated.headers["hx-redirect"]
    draft_id = UUID(re.search(r"draft=([^&]+)", location).group(1))
    assert settings.keywords_path.read_text() == before

    draft = await client.get(location)
    assert draft.status_code == 200
    assert "Unused Service 25" in draft.text
    assert "7 candidates were filtered" in draft.text
    assert "nothing has been saved or enqueued" in draft.text
    assert str(draft_id) in draft.text

    saved = await client.post(
        "/keywords/save",
        data={"text": "Edited Service\nSecond Service", "generation_id": str(draft_id)},
    )
    assert saved.status_code == 200
    assert settings.keywords_path.read_text() == "Edited Service\nSecond Service\n"
    connection = await asyncpg.connect(settings.database_url)
    try:
        accepted_at = await connection.fetchval(
            "SELECT accepted_at FROM keyword_generations WHERE id=$1", draft_id,
        )
    finally:
        await connection.close()
    assert accepted_at is not None


@pytest.mark.asyncio
async def test_adjacent_validation_and_generation_error(app_client, monkeypatch):
    client, _ = app_client
    invalid = await client.post(
        "/keywords/generate", data={"mode": "adjacent", "seed_keyword": "not historical"},
    )
    assert invalid.status_code == 200
    assert "selected winner is unavailable" in invalid.text

    non_winner = await client.post(
        "/keywords/generate", data={"mode": "adjacent", "seed_keyword": "electricians"},
    )
    assert non_winner.status_code == 200
    assert "selected winner is unavailable" in non_winner.text

    async def fail(self, mode, used_keywords, seed_keyword=None):
        raise GenerationError("DeepSeek is temporarily unavailable; try again")

    monkeypatch.setattr(KeywordGenerator, "generate", fail)
    failure = await client.post("/keywords/generate", data={"mode": "broad"})
    assert failure.status_code == 200
    assert "temporarily unavailable" in failure.text


@pytest.mark.asyncio
async def test_expired_and_unknown_drafts_are_not_available(app_client):
    client, settings = app_client
    draft_id = uuid4()
    connection = await asyncpg.connect(settings.database_url)
    try:
        await connection.execute(
            """INSERT INTO keyword_generations
               (id,created_at,mode,model,keywords,excluded_count)
               VALUES ($1,NOW()-interval '25 hours','broad','test','[\"Old Service\"]',0)""",
            draft_id,
        )
    finally:
        await connection.close()
    assert (await client.get(f"/keywords?draft={draft_id}")).status_code == 404
    assert (await client.get(f"/keywords?draft={uuid4()}")).status_code == 404
