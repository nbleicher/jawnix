"""Activate a staged release on every app service. Rolls back automatically.

Usage on the host (after release-prepare.py): python3 release-activate.py <full sha>
Run it detached so a dropped SSH session cannot interrupt it:
    setsid nohup python3 release-activate.py <sha> > backups/release-<sha7>/activate.log 2>&1 < /dev/null &
Order: stop the app services, migrate to head, start them on the new image, verify.
If anything fails after migrating, downgrade to the previous revision with the new image,
then restore the previous images. A successful activation prunes all but the newest
KEEP_RELEASES releases' staging folders and rollback/release images.
"""
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import hashlib, json, re, shutil, subprocess, sys, time

root = Path("/srv/jawnix")
app = root / "app"
IMAGE_KEYS = ("PYTHON_VERSION", "PYTHON_SHA256", "GPG_KEY")
KEEP_RELEASES = 3
RELEASE_SHA = re.compile(r"[0-9a-f]{7}")

# Job failures that predate a release and are retried by the worker. Each failed job is
# logged with a traceback ("Job N failed"); list a known error's prefix here so it is not
# treated as a regression. Empty means any worker traceback fails the release.
KNOWN_JOB_ERRORS: tuple[str, ...] = ()


def run(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def sql(query):
    return run(["docker", "exec", "jawnix-postgres-1", "psql", "-U", "jawnix", "-d", "jawnix", "-At", "-c", query]).strip()


def compose(*args):
    return run(["docker", "compose", "-f", "docker-compose.yml", *args], cwd=app)


def get(path):
    try:
        with urlopen("https://jawnix.com" + path, timeout=15) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, ""


def protected(digests):
    for path, digest in digests.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, f"changed: {path}"


def declared_env(services):
    """The environment each service is configured with (docker-compose.yml plus .env)."""
    config = json.loads(compose("config", "--format", "json"))
    return {service: {key: "" if value is None else str(value)
                      for key, value in (config["services"][service].get("environment") or {}).items()}
            for service in services}


def env_check(service, old, new, declared, image_env):
    """A recreated container must have exactly the new image's built-in environment
    plus its declared Compose configuration: nothing set outside the config survives,
    and keys added, changed, or removed in .env take effect. Returns the declared keys
    whose value differs from the old container (including keys removed from .env)."""
    old_env = dict(v.split("=", 1) for v in old)
    new_env = dict(v.split("=", 1) for v in new)
    expected = dict(v.split("=", 1) for v in image_env)
    expected.update(declared)
    assert new_env == expected, (
        f"{service}: environment differs from image plus Compose configuration: "
        f"{sorted(k for k in set(new_env) | set(expected) if new_env.get(k) != expected.get(k))}"
    )
    removed = sorted(k for k in old_env if k not in new_env and k not in IMAGE_KEYS)
    return sorted(k for k in declared if old_env.get(k) != new_env.get(k)) + [f"{k} (removed)" for k in removed]


def unexpected_tracebacks(service, text):
    tracebacks = text.count("Traceback (most recent call last)")
    if service == "api":
        return tracebacks
    failed = re.findall(r"Job (\d+) failed", text)
    known = 0
    for job_id in set(failed):
        error = sql(f"SELECT last_error FROM jobs WHERE id = {int(job_id)}")
        if error.startswith(KNOWN_JOB_ERRORS):
            known += failed.count(job_id)
    return tracebacks - known


def retag(services, source_for):
    for service in services:
        run(["docker", "image", "tag", source_for(service), f"jawnix-{service}:latest"])


def prune_releases(keep=KEEP_RELEASES):
    """Remove the staging folders and rollback/release images of every release older
    than the newest `keep`. A release's age is the mtime of its backups/release-<sha7>
    or release-stage-<sha7> folder, so the order survives earlier prunes. The
    release-result evidence under backups/release-<sha7> is kept."""
    newest = {}
    for parent, prefix in ((root, "release-stage-"), (root / "backups", "release-")):
        for path in parent.glob(f"{prefix}*"):
            sha = path.name.removeprefix(prefix)
            if path.is_dir() and RELEASE_SHA.fullmatch(sha):
                newest[sha] = max(newest.get(sha, 0), path.stat().st_mtime)
    tags = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]).split()
    pruned = {"folders": [], "images": []}
    for sha in sorted(newest, key=newest.get, reverse=True)[keep:]:
        folder = root / f"release-stage-{sha}"
        if folder.is_dir():
            shutil.rmtree(folder)
            pruned["folders"].append(folder.name)
        for tag in tags:
            if tag == f"jawnix-release:{sha}" or (tag.startswith("jawnix-") and tag.endswith(f":rollback-{sha}")):
                run(["docker", "image", "rm", tag])
                pruned["images"].append(tag)
    return pruned


def main():
    source = sys.argv[1]
    short = source[:7]
    backup = root / "backups" / f"release-{short}"
    before = json.loads((backup / "release-before.json").read_text())
    image = f"jawnix-release:{short}"
    services = list(before["services"])

    protected(before["protected"])
    for service, info in before["services"].items():
        assert inspect(f"jawnix-{service}-1")["Image"] == info["image"], f"{service} changed since prepare"
    assert sql("SELECT version_num FROM alembic_version") == before["alembic"], "database revision changed since prepare"
    expected = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).strip()

    migrated = False
    started = datetime.now(timezone.utc)
    try:
        retag(services, lambda service: image)
        compose("stop", "-t", "60", *services)
        print("App services stopped; migrating.", flush=True)
        compose("run", "--rm", "--no-deps", "-T", "-e", "PGOPTIONS=-c lock_timeout=30s", "api", "alembic", "upgrade", "head")
        migrated = True
        heads = compose("run", "--rm", "--no-deps", "-T", "api", "alembic", "heads").split()
        head = heads[0]
        assert sql("SELECT version_num FROM alembic_version") == head
        compose("up", "-d", "--no-deps", *services)
        for attempt in range(90):
            if get("/api/readyz")[0] == 200:
                break
            time.sleep(1)
        else:
            raise AssertionError("API not ready")
        time.sleep(25)
        declared = declared_env(services)
        image_env = json.loads(run(["docker", "image", "inspect", image, "--format", "{{json .Config.Env}}"]))
        added = {}
        for service in services:
            row = inspect(f"jawnix-{service}-1")
            assert row["Image"] == expected and row["State"]["Running"] and row["RestartCount"] == 0, f"{service} not healthy"
            added[service] = env_check(service, before["services"][service]["env"], row["Config"]["Env"], declared[service], image_env)
            logs = subprocess.run(["docker", "logs", "--since", started.isoformat(), f"jawnix-{service}-1"],
                                  capture_output=True, text=True)
            assert unexpected_tracebacks(service, logs.stdout + logs.stderr) == 0, f"{service} logged an unexpected traceback"
        status, _ = get("/api/healthz")
        assert status == 200
        html = run(["docker", "exec", "jawnix-api-1", "cat", "/app/frontend/dist/index.html"])
        entry = re.search(r'src="([^"]*assets/[^"]+\.js)"', html).group(1)
        for path in ["/app/admin/today", "/app/home", "/app/sign-in"]:
            status, body = get(path)
            assert status == 200 and entry in body, path
        assert get("/api/admin/today")[0] in (401, 403)
        for name, container_id in before["others"].items():
            assert inspect(name)["Id"] == container_id, f"{name} was recreated"
        protected(before["protected"])
    except Exception:
        for service in services:
            saved = subprocess.run(["docker", "logs", "--since", started.isoformat(), f"jawnix-{service}-1"], capture_output=True, text=True)
            (backup / f"failed-{service}.log").write_text(saved.stdout + saved.stderr)
        if migrated:
            print("Rolling back: downgrading the database with the new image.", flush=True)
            try:
                compose("stop", "-t", "30", *services)
                compose("run", "--rm", "--no-deps", "-T", "api", "alembic", "downgrade", before["alembic"])
            except Exception as error:
                print(f"DOWNGRADE FAILED: {error}. Old images need revision {before['alembic']}; restore tables-before.sql.", flush=True)
        retag(services, lambda service: f"jawnix-{service}:rollback-{short}")
        compose("up", "-d", "--no-deps", *services)
        print("Previous images restored.", flush=True)
        raise

    result = {"source": source, "image": expected, "alembic": head, "environmentMovedToConfigured": added,
              "verifiedAt": datetime.now(timezone.utc).isoformat()}
    (backup / "release-result.json").write_text(json.dumps(result, indent=2))
    (root / "DEPLOYED_COMMIT").write_text(source + "\n")
    print(json.dumps(result, indent=2))
    try:
        pruned = prune_releases()
        if pruned["folders"] or pruned["images"]:
            print(f"Pruned old releases: {json.dumps(pruned)}", flush=True)
    except Exception as error:
        print(f"Release pruning failed (the release itself is unaffected): {error}", flush=True)


if __name__ == "__main__":
    main()
