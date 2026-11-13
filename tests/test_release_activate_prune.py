from __future__ import annotations

import importlib.util
import os
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "release_activate", Path(__file__).parents[1] / "ops" / "release-activate.py"
)
release_activate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_activate)


IMAGES = "\n".join(
    [
        "jawnix-release:aaaa001",
        "jawnix-release:bbbb002",
        "jawnix-release:cccc003",
        "jawnix-release:dddd004",
        "jawnix-api:rollback-aaaa001",
        "jawnix-worker:rollback-aaaa001",
        "jawnix-api:rollback-bbbb002",
        "jawnix-api:rollback-cccc003",
        "jawnix-api:rollback-dddd004",
        "jawnix-api:rollback-ui-overhaul",
        "jawnix-api:latest",
        "postgres:16",
    ]
)


def make_release(root: Path, sha: str, mtime: int, stage: bool = True) -> None:
    record = root / "backups" / f"release-{sha}"
    record.mkdir(parents=True)
    (record / "release-result.json").write_text("{}", encoding="utf-8")
    if stage:
        folder = root / f"release-stage-{sha}"
        folder.mkdir()
        (folder / "app.py").write_text("# staged\n", encoding="utf-8")
        os.utime(folder, (mtime, mtime))
    os.utime(record, (mtime, mtime))


def install_fake_docker(monkeypatch, calls: list[list[str]]) -> None:
    def fake_run(args, **kw):
        calls.append(args)
        if args[:3] == ["docker", "images", "--format"]:
            return IMAGES + "\n"
        return ""

    monkeypatch.setattr(release_activate, "run", fake_run)


def test_prune_keeps_only_the_newest_three_releases(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    install_fake_docker(monkeypatch, calls)
    monkeypatch.setattr(release_activate, "root", tmp_path)
    for sha, mtime in [("aaaa001", 100), ("bbbb002", 200), ("cccc003", 300), ("dddd004", 400)]:
        make_release(tmp_path, sha, mtime)

    pruned = release_activate.prune_releases()

    assert pruned["folders"] == ["release-stage-aaaa001"]
    assert pruned["images"] == ["jawnix-release:aaaa001", "jawnix-api:rollback-aaaa001", "jawnix-worker:rollback-aaaa001"]
    assert not (tmp_path / "release-stage-aaaa001").exists()
    for sha in ["bbbb002", "cccc003", "dddd004"]:
        assert (tmp_path / f"release-stage-{sha}").is_dir()
        assert (tmp_path / "backups" / f"release-{sha}").is_dir()
    removed = [args for args in calls if args[:3] == ["docker", "image", "rm"]]
    assert {args[3] for args in removed} == set(pruned["images"])


def test_prune_leaves_non_release_names_and_current_images_alone(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    install_fake_docker(monkeypatch, calls)
    monkeypatch.setattr(release_activate, "root", tmp_path)
    for sha, mtime in [("aaaa001", 100), ("bbbb002", 200), ("cccc003", 300), ("dddd004", 400)]:
        make_release(tmp_path, sha, mtime, stage=False)
    (tmp_path / "release-stage-feature-x").mkdir()

    pruned = release_activate.prune_releases()

    assert pruned["folders"] == []
    assert (tmp_path / "release-stage-feature-x").is_dir()
    removed = {args[3] for args in calls if args[:3] == ["docker", "image", "rm"]}
    assert "jawnix-api:rollback-ui-overhaul" not in removed
    assert "jawnix-api:latest" not in removed
    assert "postgres:16" not in removed


def test_prune_is_a_no_op_with_fewer_than_keep_releases(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    install_fake_docker(monkeypatch, calls)
    monkeypatch.setattr(release_activate, "root", tmp_path)
    for sha, mtime in [("cccc003", 300), ("dddd004", 400)]:
        make_release(tmp_path, sha, mtime)

    pruned = release_activate.prune_releases()

    assert pruned == {"folders": [], "images": []}
    assert not [args for args in calls if args[:3] == ["docker", "image", "rm"]]


def test_prune_orders_by_folder_mtime_so_order_survives_earlier_prunes(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    install_fake_docker(monkeypatch, calls)
    monkeypatch.setattr(release_activate, "root", tmp_path)
    for sha, mtime in [("aaaa001", 100), ("bbbb002", 200), ("cccc003", 300), ("dddd004", 400)]:
        make_release(tmp_path, sha, mtime)
    release_activate.prune_releases()
    calls.clear()
    make_release(tmp_path, "eeee005", 500)

    pruned = release_activate.prune_releases()

    assert pruned["folders"] == ["release-stage-bbbb002"]
    assert "jawnix-release:bbbb002" in pruned["images"]
    assert "jawnix-release:dddd004" not in pruned["images"]
    assert not (tmp_path / "release-stage-bbbb002").exists()
    assert (tmp_path / "release-stage-eeee005").is_dir()
