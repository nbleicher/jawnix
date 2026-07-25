from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from .auth import Principal, clear_session, issue_session, require_admin, require_principal, verify_supabase_token
from .config import Settings, get_settings
from .database import get_db
from .jobs import enqueue_job
from .models import Agency, Agent, BatchArtifact, CustomerProfile, LeadRequest, RequestStatus, WebhookReceipt, utcnow
from .schemas import (
    AgencyUpdate,
    AgentUpdate,
    CustomerCreate,
    ProfileOut,
    ProfileUpdate,
    RecipientMappingUpdate,
    RequestCreate,
    RequestOut,
    SessionExchange,
)
from .states import normalize_states
from .telegram import TelegramClient, parse_callback_data, verify_telegram_secret
from .transitions import TransitionError, transition_request


app = FastAPI(title="Jawnix VPS API", version="1.0.0")


@app.get("/api/healthz")
def healthz(settings: Settings = Depends(get_settings)):
    return {"ok": True, "billingEnabled": settings.billing_enabled}


@app.get("/api/readyz")
def readyz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}


@app.post("/api/auth/session")
async def create_session(
    payload: SessionExchange,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = await verify_supabase_token(payload.access_token, settings)
    role = str((user.get("app_metadata") or {}).get("jawnix_role") or "customer")
    if payload.requested_next == "/admin.html" and role != "admin":
        raise HTTPException(status_code=403, detail="Sign in with noah@jawnix.com to access administration.")
    principal = issue_session(response, user, settings)
    profile = db.get(CustomerProfile, principal.user_id)
    if profile is None and principal.role != "admin":
        metadata = user.get("user_metadata") or {}
        profile = CustomerProfile(
            user_id=principal.user_id,
            email=principal.email,
            first_name=str(metadata.get("first_name") or ""),
            last_name=str(metadata.get("last_name") or ""),
            licensed_states=[],
        )
        db.add(profile)
    elif profile is not None:
        profile.email = principal.email
    db.commit()
    return {"ok": True, "role": principal.role, "next": "/admin.html" if principal.role == "admin" else "/portal.html"}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    _: Principal = Depends(require_principal),
    settings: Settings = Depends(get_settings),
):
    clear_session(response, settings)
    return {"ok": True}


@app.get("/api/me/profile", response_model=ProfileOut)
def get_profile(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    profile = db.get(CustomerProfile, principal.user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    return profile


@app.patch("/api/me/profile", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = db.get(CustomerProfile, principal.user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    profile.first_name = payload.first_name.strip()
    profile.last_name = payload.last_name.strip()
    profile.phone = payload.phone.strip()
    profile.licensed_states = payload.licensed_states
    profile.updated_at = utcnow()
    db.commit()
    db.refresh(profile)
    return profile


@app.get("/api/me/requests", response_model=list[RequestOut])
def list_my_requests(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(LeadRequest).where(LeadRequest.user_id == principal.user_id).order_by(LeadRequest.created_at.desc())
        )
    )


@app.post("/api/me/requests", response_model=RequestOut, status_code=201)
def create_request(
    payload: RequestCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = db.get(CustomerProfile, principal.user_id)
    if (
        profile is None
        or profile.agent is None
        or not profile.agent.active
        or (profile.agent.agency is not None and not profile.agent.agency.active)
        or profile.mapping_confirmed_at is None
    ):
        raise HTTPException(
            status_code=409,
            detail="Your distribution mapping must be active and confirmed before requesting a batch.",
        )
    saved_states = normalize_states(profile.licensed_states)
    if not saved_states:
        raise HTTPException(status_code=422, detail="Save at least one licensed state first.")
    states = saved_states if payload.state_mode == "all_saved" else payload.states
    if not set(states).issubset(saved_states):
        raise HTTPException(status_code=422, detail="Request states must be selected from your saved profile states.")
    request = LeadRequest(
        user_id=principal.user_id,
        agent_id=profile.agent_id,
        lead_count=payload.lead_count,
        state_mode=payload.state_mode,
        states_snapshot=states,
        delivery_email=profile.email,
        status=RequestStatus.pending.value,
        status_message="Awaiting Telegram approval.",
    )
    db.add(request)
    db.flush()
    enqueue_job(db, "notify_request", request.id)
    db.commit()
    db.refresh(request)
    return request


@app.delete("/api/me/requests/{request_id}")
def cancel_request(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    item = db.scalar(
        select(LeadRequest)
        .where(LeadRequest.id == request_id, LeadRequest.user_id == principal.user_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request was not found.")
    if item.status != RequestStatus.pending.value:
        raise HTTPException(status_code=409, detail="Only pending requests can be canceled.")
    item.status = RequestStatus.canceled.value
    item.status_message = "Canceled by customer."
    enqueue_job(db, "update_notification", item.id)
    db.commit()
    return {"ok": True}


def _request_dict(item: LeadRequest) -> dict:
    return {
        "id": str(item.id),
        "userId": str(item.user_id),
        "customer": " ".join(part for part in (item.profile.first_name, item.profile.last_name) if part).strip() or item.profile.email,
        "email": item.delivery_email,
        "agent": item.agent.name,
        "leadCount": item.lead_count,
        "states": item.states_snapshot,
        "status": item.status,
        "availableCount": item.available_count,
        "statusMessage": item.status_message,
        "createdAt": item.created_at,
        "deliveredAt": item.delivered_at,
        "hasArtifact": item.artifact is not None,
    }


@app.get("/api/admin/requests")
def admin_requests(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return [_request_dict(item) for item in db.scalars(select(LeadRequest).order_by(LeadRequest.created_at.desc()))]


@app.get("/api/admin/recipients")
def admin_recipients(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    profiles = list(db.scalars(select(CustomerProfile).order_by(CustomerProfile.email)))
    agencies = list(db.scalars(select(Agency).order_by(Agency.name)))
    agents = list(db.scalars(select(Agent).order_by(Agent.name)))
    return {
        "recipients": [
            {
                "userId": str(profile.user_id),
                "email": profile.email,
                "name": " ".join(part for part in (profile.first_name, profile.last_name) if part).strip(),
                "states": profile.licensed_states,
                "agentId": profile.agent_id,
                "agent": profile.agent.name if profile.agent else "",
                "confirmed": profile.mapping_confirmed_at is not None,
            }
            for profile in profiles
        ],
        "agencies": [
            {"id": agency.id, "slug": agency.slug, "name": agency.name, "active": agency.active}
            for agency in agencies
        ],
        "agents": [
            {
                "id": agent.id,
                "slug": agent.slug,
                "name": agent.name,
                "active": agent.active,
                "agencyId": agent.agency_id,
                "agency": agent.agency.name if agent.agency else "",
            }
            for agent in agents
        ],
    }


@app.post("/api/admin/recipients/sync")
async def sync_recipients(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    agents = {agent.slug: agent for agent in db.scalars(select(Agent).where(Agent.active.is_(True)))}
    seen = created = proposed = 0
    page = 1
    while True:
        data = await _supabase_admin(settings, "GET", f"/auth/v1/admin/users?page={page}&per_page=1000")
        users = data.get("users") or []
        if not users:
            break
        for user in users:
            role = str((user.get("app_metadata") or {}).get("jawnix_role") or "customer")
            if role != "customer":
                continue
            seen += 1
            user_id = uuid.UUID(str(user["id"]))
            email = str(user.get("email") or "").strip().lower()
            metadata = user.get("user_metadata") or {}
            profile = db.get(CustomerProfile, user_id)
            is_new = profile is None
            if profile is None:
                profile = CustomerProfile(
                    user_id=user_id,
                    email=email,
                    first_name=str(metadata.get("first_name") or ""),
                    last_name=str(metadata.get("last_name") or ""),
                    licensed_states=[],
                )
                db.add(profile)
                created += 1
            else:
                profile.email = email
            if profile.agent_id is None:
                candidates = {
                    re.sub(r"[^a-z0-9]+", "-", email.split("@", 1)[0]).strip("-"),
                    re.sub(r"[^a-z0-9]+", "-", profile.first_name.lower()).strip("-"),
                    re.sub(r"[^a-z0-9]+", "-", f"{profile.first_name} {profile.last_name}".lower()).strip("-"),
                }
                match = next((agents[value] for value in candidates if value in agents), None)
                if match:
                    profile.agent_id = match.id
                    profile.mapping_confirmed_at = None
                    proposed += 1
            if is_new:
                profile.mapping_confirmed_at = None
        if len(users) < 1000:
            break
        page += 1
    db.commit()
    return {"seen": seen, "created": created, "proposedMappings": proposed, "allMappingsRequireConfirmation": True}


@app.patch("/api/admin/recipients/{user_id}")
def map_recipient(
    user_id: uuid.UUID,
    payload: RecipientMappingUpdate,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    profile = db.get(CustomerProfile, user_id)
    agent = db.get(Agent, payload.agent_id)
    if profile is None or agent is None:
        raise HTTPException(status_code=404, detail="Recipient or agent was not found.")
    if not agent.active or (agent.agency is not None and not agent.agency.active):
        raise HTTPException(status_code=409, detail="Recipients can only be mapped to an active agent and agency.")
    profile.agent_id = agent.id
    profile.mapping_confirmed_at = datetime.now(timezone.utc) if payload.confirmed else None
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/agencies/{agency_id}")
def update_agency(
    agency_id: int,
    payload: AgencyUpdate,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Agency name is required.")
    agency.name = name
    agency.active = payload.active
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/agents/{agent_id}")
def update_agent(
    agent_id: int,
    payload: AgentUpdate,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent was not found.")
    agency = db.get(Agency, payload.agency_id) if payload.agency_id is not None else None
    if payload.agency_id is not None and agency is None:
        raise HTTPException(status_code=404, detail="Agency was not found.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Agent name is required.")
    agent.name = name
    agent.agency_id = agency.id if agency else None
    agent.active = payload.active
    db.commit()
    return {"ok": True}


async def _supabase_admin(settings: Settings, method: str, path: str, payload: dict | None = None):
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="Supabase administration is not configured.")
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, f"{settings.supabase_url.rstrip('/')}{path}", headers=headers, json=payload)
    if response.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Supabase administration failed: {response.text}")
    return response.json()


async def _send_password_reset(settings: Settings, email: str) -> None:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(status_code=503, detail="Supabase password recovery is not configured.")
    redirect_to = f"{settings.public_base_url.rstrip('/')}/portal-accept.html"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/recover",
            params={"redirect_to": redirect_to},
            headers=headers,
            json={"email": email},
        )
    if response.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Supabase password recovery failed: {response.text}")


@app.post("/api/admin/recipients/{user_id}/send-password-reset")
async def send_recipient_password_reset(
    user_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    profile = db.get(CustomerProfile, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Recipient was not found.")
    await _send_password_reset(settings, profile.email)
    return {"ok": True, "email": profile.email}


@app.post("/api/admin/customers", status_code=201)
async def create_customer(
    payload: CustomerCreate,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    created = await _supabase_admin(
        settings,
        "POST",
        "/auth/v1/admin/users",
        {
            "email": str(payload.email).lower(),
            "password": payload.password,
            "email_confirm": True,
            "user_metadata": {"first_name": payload.first_name, "last_name": payload.last_name},
            "app_metadata": {"jawnix_role": "customer"},
        },
    )
    profile = CustomerProfile(
        user_id=uuid.UUID(str(created["id"])),
        email=str(payload.email).lower(),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        licensed_states=[],
    )
    db.add(profile)
    db.commit()
    return {"ok": True, "userId": str(profile.user_id), "mappingConfirmed": False}


@app.post("/api/admin/requests/{request_id}/{action}")
def admin_request_action(
    request_id: uuid.UUID,
    action: str,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if action not in {"approve", "reject", "retry", "retry_delivery"}:
        raise HTTPException(status_code=404, detail="Unknown action.")
    try:
        item = transition_request(db, request_id, action)
    except TransitionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    db.commit()
    return _request_dict(item)


@app.post("/api/integrations/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not verify_telegram_secret(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
        settings.telegram_webhook_secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")
    try:
        payload = await request.json()
        update_id = str(payload["update_id"])
        callback = payload.get("callback_query")
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed Telegram update.") from None
    if not callback:
        return {"ok": True, "ignored": True}
    try:
        callback_query_id = str(callback["id"])
        user_id = str(callback["from"]["id"])
        chat_id = str(callback["message"]["chat"]["id"])
        action, request_id = parse_callback_data(str(callback["data"]))
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed Telegram callback.") from None

    telegram = TelegramClient(settings)
    if user_id not in settings.telegram_approvers or chat_id != settings.telegram_chat_id:
        background_tasks.add_task(telegram.answer_callback, callback_query_id, "Not authorized")
        return {"ok": True, "ignored": True}
    try:
        with db.begin_nested():
            db.add(WebhookReceipt(provider="telegram", event_key=update_id))
            db.flush()
    except IntegrityError:
        background_tasks.add_task(telegram.answer_callback, callback_query_id, "Already processed")
        return {"ok": True, "duplicate": True}
    enqueue_job(
        db,
        "telegram_action",
        request_id,
        payload={"action": action, "callback_query_id": callback_query_id, "approver_user_id": user_id},
    )
    db.commit()
    background_tasks.add_task(telegram.answer_callback, callback_query_id, "Queued")
    return {"ok": True, "queued": True}


@app.post("/api/integrations/resend/webhook")
async def resend_webhook(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    body = await request.body()
    if not settings.resend_webhook_secret:
        raise HTTPException(status_code=503, detail="Resend webhook verification is not configured.")
    try:
        event = Webhook(settings.resend_webhook_secret).verify(body, dict(request.headers))
    except WebhookVerificationError:
        raise HTTPException(status_code=401, detail="Invalid Resend webhook signature.") from None
    event_key = request.headers.get("svix-id") or hashlib.sha256(body).hexdigest()
    try:
        with db.begin_nested():
            db.add(WebhookReceipt(provider="resend", event_key=event_key))
            db.flush()
    except IntegrityError:
        return {"ok": True, "duplicate": True}
    event_type = str(event.get("type") or "")
    message_id = str((event.get("data") or {}).get("email_id") or "")
    artifact = db.scalar(select(BatchArtifact).where(BatchArtifact.resend_message_id == message_id))
    if artifact:
        if event_type == "email.delivered":
            artifact.delivery_status = "delivered"
            artifact.last_error = ""
        elif event_type in {"email.bounced", "email.complained", "email.failed"}:
            artifact.delivery_status = "failed"
            artifact.last_error = event_type
            item = db.get(LeadRequest, artifact.request_id)
            if item:
                item.status = RequestStatus.failed.value
                item.status_message = f"Email provider reported {event_type.replace('email.', '')}."
                enqueue_job(db, "update_notification", item.id)
    db.commit()
    return {"ok": True}
