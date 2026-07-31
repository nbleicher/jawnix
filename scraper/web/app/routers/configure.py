from copy import deepcopy

import yaml
from fastapi import APIRouter, Depends, Form, HTTPException, Request

from ..auth import require_user
from .common import bridge, render

router = APIRouter(dependencies=[Depends(require_user)])


def bounded_float(form, name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(form.get(name, default))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {name}") from exc
    if not low <= value <= high:
        raise HTTPException(status_code=422, detail=f"{name} must be between {low:g} and {high:g}")
    return value


def bounded_int(form, name: str, default: int, low: int, high: int) -> int:
    value = bounded_float(form, name, default, low, high)
    if not value.is_integer():
        raise HTTPException(status_code=422, detail=f"{name} must be an integer")
    return int(value)


def parse_config(request: Request, form) -> dict:
    control = bridge(request)
    states = [str(item).lower() for item in form.getlist("states")]
    invalid = sorted(set(states) - set(control.grid.STATE_CONFIG))
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown states: {', '.join(invalid)}")
    if len(states) != len(set(states)):
        raise HTTPException(status_code=422, detail="Duplicate state")

    current = control.load_active_config()
    config = deepcopy(current)
    config["states"] = states
    config["settings"] = {
        "zoom": bounded_int(form, "zoom", current["settings"].get("zoom", 15), 1, 21),
        "radius": bounded_float(form, "radius", current["settings"].get("radius", 10000), 100, 100000),
        "depth": bounded_int(form, "depth", current["settings"].get("depth", 3), 1, 100),
        "lang": str(form.get("lang", current["settings"].get("lang", "en"))).strip()[:10] or "en",
        "fast_mode": form.get("fast_mode") == "on",
        "timeout": bounded_int(form, "timeout", current["settings"].get("timeout", 300), 1, 300),
    }
    config["queue"] = {
        "target_depth": bounded_int(form, "target_depth", current["queue"].get("target_depth", 50), 1, 10000),
        "target_per_worker": bounded_int(form, "target_per_worker", current["queue"].get("target_per_worker", 25), 1, 100),
        "min_target_depth": bounded_int(form, "min_target_depth", current["queue"].get("min_target_depth", 25), 1, 10000),
        "max_target_depth": bounded_int(form, "max_target_depth", current["queue"].get("max_target_depth", 500), 1, 100000),
        "batch_size": bounded_int(form, "batch_size", current["queue"].get("batch_size", 100), 1, 10000),
        "poll_secs": bounded_int(form, "poll_secs", current["queue"].get("poll_secs", 5), 5, 3600),
        "skip_recent_days": bounded_int(form, "skip_recent_days", current["queue"].get("skip_recent_days", 0), 0, 365),
    }
    if config["queue"]["min_target_depth"] > config["queue"]["max_target_depth"]:
        raise HTTPException(status_code=422, detail="Minimum queue depth cannot exceed maximum")
    overrides = {}
    for state in states:
        size_raw = str(form.get(f"cell_size_km_{state}", "")).strip()
        zoom_raw = str(form.get(f"zoom_{state}", "")).strip()
        values = {}
        if size_raw:
            values["cell_size_km"] = bounded_float(form, f"cell_size_km_{state}", 1, 1, 500)
        if zoom_raw:
            values["zoom"] = bounded_int(form, f"zoom_{state}", 15, 1, 21)
        if values:
            overrides[state] = values
    config["overrides"] = overrides
    config.setdefault("api_base", "http://localhost:8080")
    return config


def preview_rows(request: Request, config: dict) -> list[dict]:
    control = bridge(request)
    return [
        {"state": state, "cells": len(control.state_cells(state, config))}
        for state in config["states"]
    ]


@router.get("/configure")
async def configure_page(request: Request):
    control = bridge(request)
    config = control.load_active_config()
    return render(
        request,
        "configure.html",
        config=config,
        all_states=sorted(control.grid.STATE_CONFIG),
        preview=preview_rows(request, config),
    )


@router.post("/configure/preview")
async def configure_preview(request: Request):
    config = parse_config(request, await request.form())
    return render(request, "fragments/config_preview.html", preview=preview_rows(request, config))


@router.post("/configure/save")
async def configure_save(request: Request, enqueue: bool = Form(default=False)):
    config = parse_config(request, await request.form())
    content = yaml.safe_dump(config, sort_keys=False)
    control = bridge(request)
    await control.atomic_write(request.app.state.settings.active_states_path, content, yaml_check=True)
    if enqueue:
        await control.trigger_enqueue()
    return render(
        request,
        "fragments/config_preview.html",
        preview=preview_rows(request, config),
        saved=True,
        enqueued=enqueue,
    )
