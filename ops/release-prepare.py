"""Stage a release on the production host. Changes no running container.

Usage on the host (from /srv/jawnix, with release-<sha7>.tar.gz built by `git archive <sha>`):
    python3 release-prepare.py <full sha> [table ...]
Tables listed after the sha are dumped before activation (the ones the release's migrations change).
"""
from pathlib import Path
import hashlib, json, subprocess, sys, tarfile

source = sys.argv[1]
short = source[:7]
root = Path("/srv/jawnix")
app = root / "app"
stage = root / f"release-stage-{short}"
backup = root / "backups" / f"release-{short}"
tables = sys.argv[2:]
SERVICES = ["api", "worker", "scheduler", "imports-worker", "fulfillment-worker", "notifications-worker",
            "keyword-worker", "keyword-refill-worker"]
PROTECTED = ["/srv/jawnix/app/.env", "/srv/jawnix/app/config.js", "/opt/gms-scale/.env.control",
             "/opt/gms-scale/control/active_states.yaml"]


def run(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


backup.mkdir(mode=0o700, exist_ok=False)
stage.mkdir(mode=0o755, exist_ok=False)
with tarfile.open(root / f"release-{short}.tar.gz") as archive:
    archive.extractall(stage, filter="data")
for path in [stage, *stage.rglob("*")]:
    if path.is_dir():
        path.chmod(0o755)

before = {"source": source, "services": {}, "others": {},
          "protected": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in PROTECTED}}
for service in SERVICES:
    row = inspect(f"jawnix-{service}-1")
    before["services"][service] = {"container": row["Id"], "image": row["Image"], "env": sorted(row["Config"]["Env"])}
for name in run(["docker", "ps", "--format", "{{.Names}}"]).split():
    if name.startswith("jawnix-") and name.removeprefix("jawnix-").removesuffix("-1") not in SERVICES:
        before["others"][name] = inspect(name)["Id"]
before["alembic"] = run(["docker", "exec", "jawnix-postgres-1", "psql", "-U", "jawnix", "-d", "jawnix", "-At", "-c",
                         "SELECT version_num FROM alembic_version"]).strip()
(backup / "release-before.json").write_text(json.dumps(before, indent=2))
(backup / "release-before.json").chmod(0o600)

# The tables the release's migrations change, dumped before anything runs.
if tables:
    with open(backup / "tables-before.sql", "wb") as out:
        subprocess.run(["docker", "exec", "jawnix-postgres-1", "pg_dump", "-U", "jawnix", "-d", "jawnix",
                        *[arg for table in tables for arg in ("-t", table)]], check=True, stdout=out)
    (backup / "tables-before.sql").chmod(0o600)

for service, info in before["services"].items():
    run(["docker", "image", "tag", info["image"], f"jawnix-{service}:rollback-{short}"])

image = f"jawnix-release:{short}"
subprocess.run(["docker", "build", "--label", f"org.opencontainers.image.revision={source}", "-t", image, str(stage)],
               check=True, stdout=subprocess.DEVNULL)
print(json.dumps({"source": source, "image": image, "imageId": run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).strip(),
                  "alembicBefore": before["alembic"], "backup": str(backup)}))
