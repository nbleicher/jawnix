#!/usr/bin/env python3
"""
push_keywords.py — push a keywords.txt from your local machine to the control host
==================================================================================
Run this FROM your laptop. It validates a local keyword list, shows a diff against
what the control host currently has, uploads it atomically (temp file + mv so a
reader never sees a half-written file), and optionally fires a trigger command on
the host (e.g. an enqueue top-up or a daily orchestrate run).

The control host is the single source of the active keyword list; your laptop only
pushes to it.

Config resolution (first found wins):
  1. CLI flags (--host/--user/...)
  2. a `control:` block in config.yaml next to this repo
  3. environment variables CONTROL_HOST / CONTROL_USER / CONTROL_PASSWORD / CONTROL_KEY

config.yaml example:
  control:
    host: 203.0.113.10
    user: root
    key: ~/.ssh/control_ed25519     # preferred; or password:
    ssh_port: 22
    remote_keywords: ~/scraper/keywords.txt
    trigger: "cd ~/scraper && python3 control/enqueue.py --once"

Usage:
  python3 push_keywords.py keywords.txt
  python3 push_keywords.py keywords.txt --trigger-run      # run the configured trigger after upload
  python3 push_keywords.py keywords.txt --yes              # no confirm prompt
  python3 push_keywords.py keywords.txt --local ~/scraper/keywords.txt   # no SSH; copy locally
  python3 push_keywords.py keywords.txt --dry-run
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CONFIG_FILE = REPO / "config.yaml"


# ── Keyword file validation / cleaning ────────────────────────────────────────
def load_clean_keywords(path: Path) -> tuple[list, list]:
    """Return (clean_keywords, warnings). Strips blanks/comments, dedups in order."""
    if not path.exists():
        print(f"ERROR: keyword file not found: {path}", file=sys.stderr)
        sys.exit(1)
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    seen, clean, warnings = set(), [], []
    dups = 0
    for ln in raw:
        kw = ln.strip()
        if not kw or kw.startswith("#"):
            continue
        key = kw.lower()
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        clean.append(kw)
    if dups:
        warnings.append(f"{dups} duplicate keyword(s) removed")
    if not clean:
        print("ERROR: no usable keywords after cleaning (file empty or all comments).",
              file=sys.stderr)
        sys.exit(1)
    return clean, warnings


def diff_keywords(new: list, old: list) -> tuple[list, list]:
    old_l = {k.lower() for k in old}
    new_l = {k.lower() for k in new}
    added   = [k for k in new if k.lower() not in old_l]
    removed = [k for k in old if k.lower() not in new_l]
    return added, removed


def print_diff(new: list, old: list):
    added, removed = diff_keywords(new, old)
    print(f"\n  Remote currently has : {len(old)} keyword(s)")
    print(f"  New list             : {len(new)} keyword(s)")
    print(f"  + added   : {len(added)}")
    print(f"  - removed : {len(removed)}")
    sample = lambda xs: ", ".join(xs[:8]) + (" …" if len(xs) > 8 else "")
    if added:
        print(f"    + {sample(added)}")
    if removed:
        print(f"    - {sample(removed)}")


# ── Config ────────────────────────────────────────────────────────────────────
def load_control_cfg(args) -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            import yaml
            data = yaml.safe_load(CONFIG_FILE.read_text()) or {}
            cfg = dict(data.get("control", {}) or {})
        except Exception:
            pass
    # env overrides
    for env, key in [("CONTROL_HOST", "host"), ("CONTROL_USER", "user"),
                     ("CONTROL_PASSWORD", "password"), ("CONTROL_KEY", "key")]:
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    # CLI overrides
    for a in ("host", "user", "password", "key", "ssh_port", "remote_keywords", "trigger"):
        v = getattr(args, a, None)
        if v:
            cfg[a] = v
    cfg.setdefault("ssh_port", 22)
    cfg.setdefault("remote_keywords", "~/scraper/keywords.txt")
    return cfg


# ── Remote ops (paramiko) ─────────────────────────────────────────────────────
def ssh_client(cfg: dict):
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(hostname=cfg["host"], port=int(cfg.get("ssh_port", 22)),
                  username=cfg.get("user", "root"), timeout=30)
    if cfg.get("key"):
        kwargs["key_filename"] = str(Path(cfg["key"]).expanduser())
    elif cfg.get("password"):
        kwargs["password"] = cfg["password"]
    c.connect(**kwargs)
    return c


def remote_read(c, remote_path: str) -> list:
    sftp = c.open_sftp()
    try:
        with sftp.open(remote_path, "r") as f:
            data = f.read().decode("utf-8", "replace")
        return [l.strip() for l in data.splitlines()
                if l.strip() and not l.strip().startswith("#")]
    except FileNotFoundError:
        return []
    finally:
        sftp.close()


def remote_home(c) -> str:
    _, stdout, stderr = c.exec_command("printf '%s' \"$HOME\"")
    code = stdout.channel.recv_exit_status()
    home = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if code != 0 or not home:
        raise RuntimeError(
            f"could not resolve remote $HOME: {err or 'empty output'}"
        )
    return home


def resolve_remote_path(c, remote_path: str) -> str:
    """Expand shell-style home paths before handing them to SFTP."""
    if remote_path == "~":
        return remote_home(c)
    if remote_path.startswith("~/"):
        return remote_home(c).rstrip("/") + remote_path[1:]
    if remote_path == "$HOME":
        return remote_home(c)
    if remote_path.startswith("$HOME/"):
        return remote_home(c).rstrip("/") + remote_path[len("$HOME"):]
    return remote_path


def remote_write_atomic(c, remote_path: str, content: str):
    """Write to <path>.tmp then mv over <path> so readers never see a partial file."""
    import shlex
    sftp = c.open_sftp()
    tmp = remote_path + ".tmp"
    try:
        # ensure parent dir
        parent = os.path.dirname(remote_path) or "."
        c.exec_command(f"mkdir -p {shlex.quote(parent)}")
        with sftp.open(tmp, "w") as f:
            f.write(content)
    finally:
        sftp.close()
    _, stdout, stderr = c.exec_command(f"mv {shlex.quote(tmp)} {shlex.quote(remote_path)}")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"atomic mv failed: {stderr.read().decode().strip()}")


def run_trigger(c, trigger: str):
    print(f"\n  Trigger: {trigger}")
    _, stdout, stderr = c.exec_command(trigger)
    for line in iter(stdout.readline, ""):
        print("    " + line.rstrip())
    code = stdout.channel.recv_exit_status()
    err = stderr.read().decode().strip()
    if err:
        print("    [stderr] " + err)
    print(f"  Trigger exit code: {code}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Push keywords.txt to the control host",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("keywords", nargs="?", default=str(REPO / "keywords.txt"),
                    help="Local keywords file (default: ./keywords.txt)")
    ap.add_argument("--local", metavar="DEST",
                    help="No SSH — validate and copy to this local path (current Mac-orchestrator)")
    ap.add_argument("--host"); ap.add_argument("--user"); ap.add_argument("--password")
    ap.add_argument("--key"); ap.add_argument("--ssh-port", dest="ssh_port", type=int)
    ap.add_argument("--remote-path", dest="remote_keywords")
    ap.add_argument("--trigger", help="Override the trigger command")
    ap.add_argument("--trigger-run", action="store_true", help="Run the trigger after upload")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation")
    ap.add_argument("--dry-run", action="store_true", help="Validate + show diff, change nothing")
    args = ap.parse_args()

    local_path = Path(args.keywords).expanduser()
    clean, warnings = load_clean_keywords(local_path)
    print(f"  Local file : {local_path}")
    print(f"  Keywords   : {len(clean)} after cleaning")
    for w in warnings:
        print(f"  note: {w}")
    content = "\n".join(clean) + "\n"

    # ── Local copy mode (no SSH) ──────────────────────────────────────────────
    if args.local:
        dest = Path(args.local).expanduser()
        old = ([l.strip() for l in dest.read_text().splitlines()
                if l.strip() and not l.strip().startswith("#")] if dest.exists() else [])
        print_diff(clean, old)
        if args.dry_run:
            print("\n  [dry-run] would write →", dest); return
        if not args.yes and input("\n  Write locally? [y/N]: ").strip().lower() != "y":
            print("  Aborted."); return
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        shutil.move(str(tmp), str(dest))
        print(f"  Wrote {len(clean)} keywords → {dest}")
        return

    # ── Remote push mode ──────────────────────────────────────────────────────
    cfg = load_control_cfg(args)
    if not cfg.get("host"):
        print("\nERROR: no control host configured. Add a `control:` block to config.yaml,\n"
              "set CONTROL_HOST/CONTROL_USER env vars, or pass --host/--user.\n"
              "(Or use --local <path> to write to a local orchestrator.)", file=sys.stderr)
        sys.exit(2)

    print(f"  Control host : {cfg.get('user','root')}@{cfg['host']}:{cfg.get('ssh_port',22)}")
    print(f"  Remote path  : {cfg['remote_keywords']}")
    try:
        c = ssh_client(cfg)
    except Exception as e:
        print(f"\nERROR: SSH connect failed: {e}", file=sys.stderr); sys.exit(1)

    try:
        resolved_remote = resolve_remote_path(c, cfg["remote_keywords"])
        if resolved_remote != cfg["remote_keywords"]:
            print(f"  Resolved path: {resolved_remote}")
        old = remote_read(c, resolved_remote)
        print_diff(clean, old)
        if args.dry_run:
            print("\n  [dry-run] would upload + (trigger if requested). No changes made.")
            return
        if not args.yes and input("\n  Push to control host? [y/N]: ").strip().lower() != "y":
            print("  Aborted."); return
        remote_write_atomic(c, resolved_remote, content)
        print(
            f"  Uploaded {len(clean)} keywords "
            f"→ {cfg['host']}:{resolved_remote}"
        )
        trigger = args.trigger or cfg.get("trigger")
        if args.trigger_run and trigger:
            run_trigger(c, trigger)
        elif trigger:
            print(f"  (trigger configured but not run — add --trigger-run to fire: {trigger})")
    finally:
        c.close()


if __name__ == "__main__":
    main()
