#!/usr/bin/env python3
"""
organize.py — keyword/state/date splitter for data analysis
============================================================
Reorganizes RAW scraper CSVs into one folder per (state, keyword, date):

    fl_plumbers_2026-06-16/
        data.csv        # deduped title,phone
        manifest.json   # counts + provenance for analysis tooling

A date folder pulled by orchestrate.py looks like:

    <date_folder>/            e.g. data_hub/6_16_26
        vps1/fl/results.csv
        vps1/oh/results.csv
        vps2/ut/results.csv

  - STATE   is taken from the path (the component after the VPS name, or any
            2-letter US state code found in the path).
  - KEYWORD is parsed from the raw CSV's `input_id` column, which the
            orchestrator sets to "<keyword> [lat,lon]".
  - DATE    is parsed from the date-folder name (M_D_YY → YYYY-MM-DD), or
            supplied with --date, or defaults to today.

IMPORTANT: run this BEFORE clean_csv.py. clean_csv strips every column down to
title+phone and destroys `input_id`, so the keyword can no longer be recovered.

Usage
-----
  python3 organize.py --folder /path/to/data_hub/6_16_26
  python3 organize.py --folder <date_folder> --output /path/to/scale
  python3 organize.py --folder <date_folder> --singular          # fl_plumber
  python3 organize.py --folder <date_folder> --global-dedup      # dedup across all keywords
  python3 organize.py --folder <date_folder> --date 2026-06-16   # force date
  python3 organize.py --folder <date_folder> --dry-run

Dedup key preference per record:  place_id  →  cid  →  norm(title)+norm(phone)
Output columns:                   title, phone
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date as date_cls
from pathlib import Path

# ── Column detection ──────────────────────────────────────────────────────────
TITLE_CANDIDATES    = ["title", "business-name", "company name", "company_name", "name"]
PHONE_CANDIDATES    = ["phone", "phones", "phone number", "phone_number"]
INPUT_ID_CANDIDATES = ["input_id", "id", "query", "keyword", "search"]
PLACEID_CANDIDATES  = ["place_id", "placeid"]
CID_CANDIDATES      = ["cid"]

US_STATES = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia",
    "ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj",
    "nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn","tx","ut","vt",
    "va","wa","wv","wi","wy","dc",
}

# Folders we create ourselves — never re-ingest them.
OUTPUT_DIR_RE = re.compile(r"^[a-z]{2}_.+_\d{4}-\d{2}-\d{2}$")


def find_col(headers, candidates):
    lower = [h.strip().lower() for h in (headers or [])]
    for c in candidates:
        if c in lower:
            return headers[lower.index(c)]
    return None


# ── Keyword parsing ───────────────────────────────────────────────────────────
def keyword_from_input_id(input_id: str) -> str:
    """
    Orchestrator job name format:  "<keyword> [lat,lon]"
    Native/point jobs:             "<keyword>"
    Strip a trailing " [ ... ]" coordinate tag if present.
    """
    if not input_id:
        return ""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", input_id).strip()


def slugify(text: str, singular: bool = False) -> str:
    s = (text or "").strip().lower()
    if singular:
        s = singularize(s)
    s = re.sub(r"[^a-z0-9]+", "_", s)   # non-alphanumerics → underscore
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def singularize(phrase: str) -> str:
    """
    Conservative singularizer applied to the LAST word only. Handles the common
    regular cases and deliberately skips ambiguous/irregular ones to avoid
    mangling (e.g. 'business' stays 'business', 'pharmacies' → 'pharmacy').
    """
    words = phrase.split()
    if not words:
        return phrase
    w = words[-1]
    # Skip short words and known-irregular endings.
    if len(w) <= 3 or w.endswith("ss") or w.endswith("us") or w.endswith("is"):
        sing = w
    elif w.endswith("ies") and len(w) > 4:
        sing = w[:-3] + "y"          # pharmacies → pharmacy
    elif w.endswith(("ches", "shes", "xes", "ses", "zes")):
        sing = w[:-2]                # churches → church, boxes → box
    elif w.endswith("s"):
        sing = w[:-1]                # plumbers → plumber
    else:
        sing = w
    words[-1] = sing
    return " ".join(words)


# ── State + date resolution ───────────────────────────────────────────────────
def state_from_relpath(rel: Path) -> str:
    """
    Prefer the 2nd path component (vps/<state>/...); otherwise the first
    2-letter token that is a known US state code anywhere in the path.
    """
    parts = [p.lower() for p in rel.parts[:-1]]   # drop the filename
    if len(parts) >= 2 and parts[1] in US_STATES:
        return parts[1]
    for p in parts:
        if p in US_STATES:
            return p
    return "xx"


def parse_date_folder(name: str) -> str | None:
    """Parse an M_D_YY date-folder name (e.g. '6_16_26') into 'YYYY-MM-DD'."""
    m = re.fullmatch(r"(\d{1,2})_(\d{1,2})_(\d{2,4})", name.strip())
    if not m:
        return None
    mo, d, y = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    try:
        return date_cls(y, mo, d).isoformat()
    except ValueError:
        return None


# ── Normalization for dedup ───────────────────────────────────────────────────
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def identity_keys(row: dict, cols: dict) -> list:
    """
    Return ALL stable identifiers for a record. A record is a duplicate if any
    of its keys was already seen (so a row carrying place_id matches a later row
    that only carries the same cid). title+phone is used ONLY as a last resort
    when no place_id/cid exists, to avoid collapsing distinct locations that
    happen to share a name+phone (e.g. chains with a shared call center).
    """
    keys = []
    pid = (row.get(cols["place_id"], "") if cols["place_id"] else "").strip()
    cid = (row.get(cols["cid"], "") if cols["cid"] else "").strip()
    if pid:
        keys.append("pid:" + pid)
    if cid:
        keys.append("cid:" + cid)
    if not keys:
        title = row.get(cols["title"], "") if cols["title"] else ""
        phone = row.get(cols["phone"], "") if cols["phone"] else ""
        keys.append("tp:" + norm(title) + "|" + norm(phone))
    return keys


# ── Core ──────────────────────────────────────────────────────────────────────
def collect(folder: Path, singular: bool, log):
    """
    Walk the date folder, returning:
        buckets: { (state, keyword_slug): [ {title, phone, _key}, ... ] }
        stats:   per-file diagnostics
    """
    buckets = defaultdict(list)
    files_ok = files_skipped = 0

    for csv_path in sorted(folder.rglob("*.csv")):
        rel = csv_path.relative_to(folder)
        name = csv_path.name

        # Skip our own outputs and clean_csv artifacts.
        if name.startswith("split_") or name == "all_combined.csv":
            continue
        if any(OUTPUT_DIR_RE.match(part) for part in rel.parts[:-1]):
            continue
        if "archive" in [p.lower() for p in rel.parts]:
            continue

        try:
            with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                headers = list(reader.fieldnames or [])
                cols = {
                    "title":    find_col(headers, TITLE_CANDIDATES),
                    "phone":    find_col(headers, PHONE_CANDIDATES),
                    "input_id": find_col(headers, INPUT_ID_CANDIDATES),
                    "place_id": find_col(headers, PLACEID_CANDIDATES),
                    "cid":      find_col(headers, CID_CANDIDATES),
                }
                if not cols["title"] and not cols["phone"]:
                    log(f"  SKIP  {rel}: no title/phone columns in {headers[:6]}")
                    files_skipped += 1
                    continue
                if not cols["input_id"]:
                    log(f"  WARN  {rel}: no input_id column — keyword unknown "
                        f"(already cleaned?). Rows go to '<state>_unknown_<date>'.")

                state = state_from_relpath(rel)
                rows_in = 0
                for r in reader:
                    title = (r.get(cols["title"], "") if cols["title"] else "").strip()
                    phone = (r.get(cols["phone"], "") if cols["phone"] else "").strip()
                    if not title and not phone:
                        continue
                    raw_kw = keyword_from_input_id(r.get(cols["input_id"], "")) if cols["input_id"] else ""
                    kw_slug = slugify(raw_kw, singular=singular)
                    buckets[(state, kw_slug)].append({
                        "title": title,
                        "phone": phone,
                        "_keys": identity_keys(r, cols),
                    })
                    rows_in += 1
            log(f"  OK    {rel}: {rows_in:,} rows  (state={state})")
            files_ok += 1
        except Exception as e:
            log(f"  FAIL  {rel}: {e}")
            files_skipped += 1

    return buckets, files_ok, files_skipped


def write_buckets(buckets, out_root: Path, date_str: str, global_dedup: bool, dry_run: bool, log):
    summary = []
    seen_global = set()

    for (state, kw_slug), rows in sorted(buckets.items()):
        folder_name = f"{state}_{kw_slug}_{date_str}"
        dest = out_root / folder_name

        seen = seen_global if global_dedup else set()
        deduped = []
        dups = 0
        for row in rows:
            keys = row["_keys"]
            if any(k in seen for k in keys):
                dups += 1
                seen.update(keys)   # link all identifiers of this record
                continue
            seen.update(keys)
            deduped.append(row)

        summary.append({
            "folder": folder_name,
            "state": state,
            "keyword": kw_slug,
            "date": date_str,
            "raw_rows": len(rows),
            "unique_rows": len(deduped),
            "duplicates_removed": dups,
        })

        if dry_run:
            log(f"  [dry-run] {folder_name}: {len(deduped):,} unique "
                f"({dups:,} dup) → {dest}")
            continue

        dest.mkdir(parents=True, exist_ok=True)
        data_path = dest / "data.csv"
        with open(data_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["title", "phone"], lineterminator="\n")
            w.writeheader()
            for row in deduped:
                w.writerow({"title": row["title"], "phone": row["phone"]})

        manifest = {
            "state": state,
            "keyword": kw_slug,
            "date": date_str,
            "raw_rows": len(rows),
            "unique_rows": len(deduped),
            "duplicates_removed": dups,
            "dedup_scope": "global" if global_dedup else "folder",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        with open(dest / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        log(f"  WROTE {folder_name}: {len(deduped):,} unique "
            f"({dups:,} dup removed)")

    return summary


def main():
    ap = argparse.ArgumentParser(
        description="Split raw scraper CSVs into {state}_{keyword}_{date} folders.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--folder", required=True, help="Date folder to process (e.g. data_hub/6_16_26)")
    ap.add_argument("--output", help="Where to write folders (default: inside the date folder)")
    ap.add_argument("--date", help="Force date as YYYY-MM-DD (default: parsed from folder name)")
    ap.add_argument("--singular", action="store_true", help="Singularize keyword (plumbers→plumber)")
    ap.add_argument("--global-dedup", action="store_true", help="Dedup across all keywords, not per-folder")
    ap.add_argument("--dry-run", action="store_true", help="Print plan, write nothing")
    args = ap.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"ERROR: not a folder: {folder}", file=sys.stderr)
        sys.exit(1)

    # Resolve date: --date > parsed folder name > today
    date_str = args.date or parse_date_folder(folder.name) or date_cls.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        print(f"ERROR: bad --date (need YYYY-MM-DD): {date_str}", file=sys.stderr)
        sys.exit(1)

    out_root = Path(args.output).expanduser().resolve() if args.output else folder

    def log(msg): print(msg)

    log(f"\n[organize] folder = {folder}")
    log(f"[organize] output = {out_root}")
    log(f"[organize] date   = {date_str}   singular={args.singular}   "
        f"dedup={'global' if args.global_dedup else 'per-folder'}\n")

    buckets, files_ok, files_skipped = collect(folder, args.singular, log)
    if not buckets:
        log("\n  No rows collected — nothing to write.")
        sys.exit(0)

    log("")
    summary = write_buckets(buckets, out_root, date_str, args.global_dedup, args.dry_run, log)

    # Date-level index for analysis tooling.
    total_unique = sum(s["unique_rows"] for s in summary)
    total_raw    = sum(s["raw_rows"] for s in summary)
    if not args.dry_run:
        index_path = out_root / f"index_{date_str}.csv"
        with open(index_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["folder", "state", "keyword", "date",
                                              "raw_rows", "unique_rows", "duplicates_removed"])
            w.writeheader()
            w.writerows(summary)
        log(f"\n  Wrote index → {index_path.name}")

    log("\n" + "=" * 60)
    log(f"  {len(summary)} keyword folders | "
        f"{total_unique:,} unique / {total_raw:,} raw rows | "
        f"files: {files_ok} ok, {files_skipped} skipped")
    log("=" * 60 + "\n")


if __name__ == "__main__":
    main()
