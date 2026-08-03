from __future__ import annotations

import os
import subprocess
from pathlib import Path


DEPLOY_SCRIPT = Path(__file__).parents[1] / "ops" / "deploy.sh"


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def prepare_release(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    remote = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    fake_bin = tmp_path / "bin"
    rsync_log = tmp_path / "rsync.log"
    remote.mkdir()
    checkout.mkdir()
    fake_bin.mkdir()

    run(["git", "init", "--bare"], remote)
    run(["git", "init", "--initial-branch=main"], checkout)
    run(["git", "config", "user.name", "Deploy Test"], checkout)
    run(["git", "config", "user.email", "deploy@example.com"], checkout)
    (checkout / "tracked.txt").write_text("release\n", encoding="utf-8")
    run(["git", "add", "tracked.txt"], checkout)
    run(["git", "commit", "-m", "Release"], checkout)
    run(["git", "remote", "add", "origin", str(remote)], checkout)
    run(["git", "tag", "production-test"], checkout)
    run(["git", "push", "--set-upstream", "origin", "main", "--tags"], checkout)

    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >>\"$RSYNC_LOG\"\n"
        "case \" $* \" in\n"
        "  *' --dry-run '*) echo '>f+++++++++ tracked.txt' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "JAWNIX_DEPLOY_TARGET": "deploy@example.test",
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "RSYNC_LOG": str(rsync_log),
        }
    )
    return checkout, rsync_log, environment


def deploy(checkout: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(DEPLOY_SCRIPT), "production-test"],
        cwd=checkout,
        env=environment,
        input="deploy production-test\n",
        capture_output=True,
        text=True,
    )


def test_deploy_dry_runs_with_pinned_excludes_before_sync(tmp_path):
    checkout, rsync_log, environment = prepare_release(tmp_path)

    result = deploy(checkout, environment)

    assert result.returncode == 0, result.stderr
    assert ">f+++++++++ tracked.txt" in result.stdout
    calls = rsync_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    assert "--dry-run" in calls[0]
    assert "--dry-run" not in calls[1]
    for call in calls:
        assert "--delete" in call
        assert "--exclude=/.git" in call
        assert "--exclude=/.env" in call
        assert "--exclude=/config.js" in call
        assert "--exclude=/.venv/" in call
        assert "--exclude=/jawnix_vps.egg-info/" in call
        assert "--exclude=/jawnix-dev.db" in call
        assert "--exclude=/batches/" in call
        assert "--exclude=/backups/" in call
        assert "--exclude=/invoices/" in call
        assert "--exclude=/monitoring/" in call
        assert "--exclude=/restic-repository/" in call
        assert "--exclude=/migration/" in call
        assert "--exclude=/user-account-migration/" in call


def test_deploy_refuses_a_dirty_checkout_before_rsync(tmp_path):
    checkout, rsync_log, environment = prepare_release(tmp_path)
    (checkout / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    result = deploy(checkout, environment)

    assert result.returncode == 2
    assert "dirty checkout" in result.stderr
    assert not rsync_log.exists()


def test_deploy_refuses_when_head_does_not_match_tag(tmp_path):
    checkout, rsync_log, environment = prepare_release(tmp_path)
    (checkout / "tracked.txt").write_text("next\n", encoding="utf-8")
    run(["git", "add", "tracked.txt"], checkout)
    run(["git", "commit", "-m", "Next"], checkout)
    run(["git", "push", "origin", "main"], checkout)

    result = deploy(checkout, environment)

    assert result.returncode == 2
    assert "HEAD is not the requested release tag" in result.stderr
    assert not rsync_log.exists()
