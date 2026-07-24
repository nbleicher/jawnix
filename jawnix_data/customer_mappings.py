from __future__ import annotations

import csv
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from jawnix.config import Settings
from jawnix.models import Agent, CustomerProfile


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ValueError("Supabase URL and service-role key are required.")
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


def _request(settings: Settings, method: str, path: str, payload: dict | None = None) -> dict:
    response = httpx.request(
        method,
        f"{settings.supabase_url.rstrip('/')}{path}",
        headers=_headers(settings),
        json=payload,
        timeout=30,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Supabase {method} {path} failed with HTTP {response.status_code}: {response.text}")
    return response.json()


def _auth_users(settings: Settings) -> dict[str, dict]:
    users: dict[str, dict] = {}
    page = 1
    while True:
        data = _request(settings, "GET", f"/auth/v1/admin/users?page={page}&per_page=1000")
        batch = data.get("users") or []
        for user in batch:
            email = str(user.get("email") or "").strip().lower()
            if email:
                users[email] = user
        if len(batch) < 1000:
            return users
        page += 1


def _invite_customer(settings: Settings, email: str) -> dict:
    redirect_to = quote(f"{settings.public_base_url.rstrip('/')}/login.html", safe="")
    return _request(
        settings,
        "POST",
        f"/auth/v1/invite?redirect_to={redirect_to}",
        {"email": email, "data": {"jawnix_role": "customer"}},
    )


def provision_customer_mappings(
    session: Session,
    settings: Settings,
    path: Path,
    *,
    invite_missing: bool = False,
) -> dict:
    users = _auth_users(settings)
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    required = {"customer_email", "agent_slug", "agency_slug", "confirmed"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Customer mapping CSV must contain customer_email, agent_slug, agency_slug, confirmed.")

    invited = created = updated = 0
    missing_users: list[str] = []
    for row in rows:
        email = str(row["customer_email"]).strip().lower()
        agent_slug = str(row["agent_slug"]).strip()
        agency_slug = str(row["agency_slug"]).strip()
        confirmed = str(row["confirmed"]).strip().lower() in {"1", "true", "yes"}
        agent = session.scalar(select(Agent).where(Agent.slug == agent_slug))
        if agent is None or not agent.active:
            raise ValueError(f"Configured agent does not exist or is inactive: {agent_slug}")
        actual_agency = agent.agency.slug if agent.agency else ""
        if actual_agency != agency_slug:
            raise ValueError(
                f"Agency mismatch for {agent_slug}: mapping requires {agency_slug}, database has {actual_agency or 'independent'}"
            )

        user = users.get(email)
        if user is None and invite_missing:
            user = _invite_customer(settings, email)
            users[email] = user
            invited += 1
        if user is None:
            missing_users.append(email)
            continue

        user_id = uuid.UUID(str(user["id"]))
        profile = session.get(CustomerProfile, user_id)
        if profile is None:
            profile = CustomerProfile(user_id=user_id, email=email, licensed_states=[])
            session.add(profile)
            created += 1
        else:
            profile.email = email
            updated += 1
        profile.agent_id = agent.id
        profile.mapping_confirmed_at = datetime.now(timezone.utc) if confirmed else None

    session.flush()
    return {
        "rows": len(rows),
        "invited": invited,
        "profilesCreated": created,
        "profilesUpdated": updated,
        "missingUsers": missing_users,
    }
