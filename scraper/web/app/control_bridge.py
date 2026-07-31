from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_locks: dict[Path, asyncio.Lock] = {}


class ControlBridge:
    def __init__(self, settings):
        self.settings = settings
        self.grid = self._load_module("gms_control_grid", settings.control_dir / "grid.py")
        self.export_leads = self._load_module(
            "gms_control_export_leads", settings.control_dir / "export_leads.py"
        )

    @staticmethod
    def _load_module(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load control helper: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def load_active_config(self) -> dict:
        data = yaml.safe_load(self.settings.active_states_path.read_text(encoding="utf-8")) or {}
        data.setdefault("states", [])
        data.setdefault("overrides", {})
        data.setdefault("settings", {})
        data.setdefault("queue", {})
        return data

    def active_states(self) -> list[str]:
        return [str(state).lower() for state in self.load_active_config()["states"]]

    def state_cells(self, state: str, config: dict | None = None) -> list[str]:
        state = state.lower()
        if state not in self.grid.STATE_CONFIG:
            raise KeyError(state)
        config = config or self.load_active_config()
        bbox, default_size = self.grid.STATE_CONFIG[state]
        cell_size = float(config.get("overrides", {}).get(state, {}).get("cell_size_km", default_size))
        return self.grid.generate_grid_cells(bbox, cell_size)

    @staticmethod
    def parse_keywords(text: str) -> list[str]:
        keywords: list[str] = []
        seen: set[str] = set()
        for line in text.splitlines():
            keyword = line.strip()
            key = keyword.casefold()
            if keyword and not keyword.startswith("#") and key not in seen:
                seen.add(key)
                keywords.append(keyword)
        return keywords

    def load_keywords(self) -> list[str]:
        if not self.settings.keywords_path.exists():
            return []
        return self.parse_keywords(self.settings.keywords_path.read_text(encoding="utf-8"))

    async def atomic_write(self, path: Path, content: str, yaml_check: bool = False) -> None:
        lock = _locks.setdefault(path, asyncio.Lock())
        async with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if yaml_check:
                yaml.safe_load(content)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            if path.exists():
                shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}"))
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            try:
                with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if yaml_check:
                    yaml.safe_load(tmp.read_text(encoding="utf-8"))
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)

    async def trigger_enqueue(self) -> None:
        mode = self.settings.enqueue_trigger_mode
        if mode == "none":
            return
        if mode == "sentinel":
            trigger = self.settings.enqueue_trigger_path
            trigger.parent.mkdir(parents=True, exist_ok=True)
            trigger.touch()
            return
        if mode == "subprocess":
            await asyncio.create_subprocess_exec(
                sys.executable,
                str(self.settings.control_dir / "enqueue.py"),
                "--once",
                "--states",
                str(self.settings.active_states_path),
                "--keywords",
                str(self.settings.keywords_path),
            )
            return
        raise RuntimeError(f"unsupported enqueue trigger mode: {mode}")

    def pipeline_paused(self) -> bool:
        return self.settings.pipeline_pause_path.exists()

    def pipeline_pause_info(self) -> dict:
        path = self.settings.pipeline_pause_path
        if not path.exists():
            return {"mode": None, "cancelled_jobs": 0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mode = data.get("mode")
            if mode not in {"drain", "clear"}:
                raise ValueError("invalid pause mode")
            return {"mode": mode, "cancelled_jobs": max(0, int(data.get("cancelled_jobs", 0)))}
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {"mode": "drain", "cancelled_jobs": 0}

    async def set_pipeline_paused(
        self, paused: bool, *, mode: str = "drain", cancelled_jobs: int = 0,
    ) -> None:
        path = self.settings.pipeline_pause_path
        lock = _locks.setdefault(path, asyncio.Lock())
        async with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if paused:
                if mode not in {"drain", "clear"}:
                    raise ValueError("invalid pause mode")
                payload = json.dumps({"mode": mode, "cancelled_jobs": max(0, cancelled_jobs)}) + "\n"
                tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                try:
                    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                path.unlink(missing_ok=True)
