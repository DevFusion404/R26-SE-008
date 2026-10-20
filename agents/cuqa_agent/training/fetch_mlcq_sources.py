"""
fetch_mlcq_sources.py
---------------------
Downloads the Java source files referenced by the MLCQ dataset.

MLCQCodeSmellSamples.csv only stores labels and GitHub links. Each row points
to a file in a public repository at a fixed commit. This script fetches every
unique (repository, commit, path) once and stores it in a local cache so that
build_dataset.py can extract features from the real code.

The downloaded files are treated as data only. They are parsed, never executed.

Usage:
    python training/fetch_mlcq_sources.py
    python training/fetch_mlcq_sources.py --workers 8 --limit 50
"""

import argparse
import csv
import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

TRAINING_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRAINING_DIR.parents[2]
DEFAULT_CSV = REPO_ROOT / "evaluation" / "data" / "MLCQCodeSmellSamples.csv"
DEFAULT_CACHE = TRAINING_DIR / "data" / "mlcq_sources"

MAX_FILE_BYTES = 3 * 1024 * 1024
REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 4
USER_AGENT = "RefactorIQ-CUQA-research/1.0 (MLCQ dataset reconstruction)"


def source_key(repository: str, commit_hash: str, path: str) -> str:
    """Stable cache key for one file version.

    A hash is used instead of the original path because the nested Java
    package paths exceed the Windows path length limit.
    """
    raw = f"{repository}|{commit_hash}|{path}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:20]


def parse_owner_repo(repository: str) -> tuple[str, str] | None:
    """Turn 'git@github.com:apache/hive.git' into ('apache', 'hive')."""
    repo = repository.strip()
    if ":" not in repo:
        return None
    tail = repo.split(":", 1)[1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    parts = tail.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def load_unique_files(csv_path: Path) -> list[dict]:
    """Read the MLCQ CSV and return one record per unique file version."""
    seen: dict[str, dict] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            repository = (row.get("repository") or "").strip()
            commit_hash = (row.get("commit_hash") or "").strip()
            path = (row.get("path") or "").strip()
            if not repository or not commit_hash or not path:
                continue
            key = source_key(repository, commit_hash, path)
            if key not in seen:
                seen[key] = {
                    "key": key,
                    "repository": repository,
                    "commit_hash": commit_hash,
                    "path": path,
                }
    return list(seen.values())


def _candidate_urls(owner: str, repo: str, commit_hash: str, path: str) -> list[str]:
    quoted = quote(path.lstrip("/"))
    return [
        f"https://raw.githubusercontent.com/{owner}/{repo}/{commit_hash}/{quoted}",
        # github.com follows repository renames, raw.githubusercontent.com does not
        f"https://github.com/{owner}/{repo}/raw/{commit_hash}/{quoted}",
    ]


def fetch_one(record: dict, cache_dir: Path, session: requests.Session) -> dict:
    """Download one file. Returns the record with status and size filled in."""
    target = cache_dir / f"{record['key']}.java"
    result = dict(record, file=target.name, status="", bytes=0)

    if target.exists() and target.stat().st_size > 0:
        result.update(status="cached", bytes=target.stat().st_size)
        return result

    owner_repo = parse_owner_repo(record["repository"])
    if owner_repo is None:
        result["status"] = "bad_repository"
        return result

    last_status = "not_found"
    for url in _candidate_urls(*owner_repo, record["commit_hash"], record["path"]):
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            except requests.RequestException:
                last_status = "network_error"
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                content = resp.content
                if len(content) > MAX_FILE_BYTES:
                    result["status"] = "too_large"
                    return result
                target.write_bytes(content)
                result.update(status="downloaded", bytes=len(content))
                return result
            if resp.status_code in (429, 500, 502, 503, 504):
                last_status = f"http_{resp.status_code}"
                time.sleep(5 * (attempt + 1))
                continue
            last_status = f"http_{resp.status_code}"
            break  # 404 and other client errors: try the next URL form

    result["status"] = last_status
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Download MLCQ source files from GitHub.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0, help="Fetch only the first N files (0 = all).")
    args = parser.parse_args()

    records = load_unique_files(args.csv)
    if args.limit:
        records = records[: args.limit]
    args.cache.mkdir(parents=True, exist_ok=True)

    print(f"Unique files to fetch: {len(records)}")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    adapter = HTTPAdapter(pool_connections=args.workers, pool_maxsize=args.workers)
    session.mount("https://", adapter)

    results: list[dict] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_one, rec, args.cache, session) for rec in records]
        for done, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if done % 250 == 0 or done == len(futures):
                ok = sum(1 for r in results if r["status"] in ("downloaded", "cached"))
                print(f"  {done}/{len(futures)} processed, {ok} available, "
                      f"{time.time() - started:.0f}s elapsed", flush=True)

    results.sort(key=lambda r: r["key"])
    index_path = args.cache / "index.csv"
    with open(index_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["key", "repository", "commit_hash", "path", "file", "status", "bytes"]
        )
        writer.writeheader()
        writer.writerows(results)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total_mb = sum(r["bytes"] for r in results) / (1024 * 1024)
    print(f"\nStatus counts: {counts}")
    print(f"Total size on disk: {total_mb:.1f} MB")
    print(f"Index written to: {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
