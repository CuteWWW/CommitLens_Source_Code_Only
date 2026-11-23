"""Local, versioned Git log cache with append-only updates when history permits.

No network access, sqlite server, or additional Python dependencies required.
Cache files are derived artifacts; they are never a source of historical truth.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .ingest import read_log


def git_head(repo: Path) -> str:
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    if proc.returncode:
        valid = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"],
                               capture_output=True, text=True, check=False)
        if valid.returncode == 0:
            return "EMPTY"
        raise RuntimeError(f"Cannot resolve HEAD: {proc.stderr.strip()}")
    return proc.stdout.strip()


def is_ancestor(repo: Path, old: str, head: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", old, head],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0


@dataclass
class CachedLog:
    text: str
    mode: str
    head: str
    key: str
    metadata_file: Path
    metadata: dict
    delta_text: str | None = None
    base_head: str | None = None


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".commitlens-", delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(payload)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


def fetch_git_log(
    repo: Path,
    cache_dir: Path,
    *,
    since_days: int | None,
    until: str | None,
    all_branches: bool,
    include_merges: bool,
) -> CachedLog:
    """Get cached history, delta-fetch if HEAD moved forward, or fetch afresh.

    For --all-branches, the union of branch tips may change without HEAD moving,
    so a full refresh is mandatory to avoid stale results.
    """
    repo = repo.resolve()
    scope = {"repo": str(repo), "since_days": since_days, "until": until,
             "all_branches": all_branches, "include_merges": include_merges,
             "calendar_day": datetime.now(timezone.utc).date().isoformat()
             if since_days else None}
    key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:20]
    meta_file = cache_dir / f"{key}.json"
    data_file = cache_dir / f"{key}.log.gz"
    meta = {}
    if meta_file.is_file() and data_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("scope") != scope or meta.get("format_version") != 1:
                meta = {}
        except (ValueError, OSError):
            meta = {}

    head = git_head(repo)
    mode = "full"
    old_text = None
    if meta and not all_branches:
        try:
            with gzip.open(data_file, "rt", encoding="utf-8") as f:
                old_text = f.read()
            if hashlib.sha256(old_text.encode()).hexdigest() != meta.get("data_sha256"):
                old_text = None
        except (OSError, UnicodeError):
            old_text = None

    if old_text is not None and meta.get("head") == head:
        return CachedLog(old_text, "unchanged", head, key, meta_file, meta)

    previous_head = meta.get("head") if meta else None
    if old_text is not None and is_ancestor(repo, meta["head"], head):
        delta = read_log(repo, since_days=since_days, until=until,
                         all_branches=False, include_merges=include_merges,
                         exclude_rev=meta["head"])
        # Records use the explicit HEAD marker and are safe to concatenate.
        text = delta + old_text
        mode = "delta"
    else:
        text = read_log(repo, since_days=since_days, until=until,
                        all_branches=all_branches, include_merges=include_merges)

    meta = {"format_version": 1, "scope": scope, "head": head,
            "data_sha256": hashlib.sha256(text.encode()).hexdigest()}
    _atomic_write(data_file, gzip.compress(text.encode("utf-8"), compresslevel=3))
    _atomic_write(meta_file, json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
    return CachedLog(text, mode, head, key, meta_file, meta,
                     delta_text=delta if mode == "delta" else None,
                     base_head=previous_head if mode == "delta" else None)


def store_render_checkpoint(cache: CachedLog, spec: dict, html: Path,
                            json_path: Path | None, severity_rank: int) -> None:
    """Only mark completed outputs after both report files were written."""
    meta = dict(cache.metadata)
    meta["render"] = {
        "spec": spec,
        "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
        "json_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest() if json_path else None,
        "severity_rank": severity_rank,
    }
    _atomic_write(cache.metadata_file,
                  json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))


def can_reuse_render(cache: CachedLog, spec: dict, html: Path, json_path: Path | None) -> int | None:
    """Return stored worst severity on an exact same-day, same-HEAD, same-file hit."""
    if cache.mode != "unchanged":
        return None
    record = cache.metadata.get("render", {})
    if record.get("spec") != spec or not html.is_file():
        return None
    if hashlib.sha256(html.read_bytes()).hexdigest() != record.get("html_sha256"):
        return None
    if json_path:
        if not json_path.is_file():
            return None
        if hashlib.sha256(json_path.read_bytes()).hexdigest() != record.get("json_sha256"):
            return None
    return record.get("severity_rank")
