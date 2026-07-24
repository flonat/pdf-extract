"""Bulk Marker extraction cron — fills the pdf-extract cache for a
Paperpile library, prioritised by relevance to active projects.

Designed to run nightly via launchd in a time-bounded window. Each run:
  1. Loads Paperpile JSON export.
  2. Builds priority queue (papers in active project bibs first, then long tail).
  3. Skips papers already in the marker cache.
  4. Runs `extract(..., backend='marker')` per paper until time budget exhausted.
  5. Logs structured progress to stdout (captured by launchd to log file).

Cache directory: see `pdf_extract.cache._default_cache_root()`. On macOS this
defaults to `/Volumes/SSD/pdf-extract-cache/` if that volume exists, else
`~/.cache/pdf-extract/`. Override with `PDF_EXTRACT_CACHE_DIR=...`.

Configuration is via env vars (with CLI overrides). All paths must be set —
this script intentionally has no hardcoded user paths so it works on any
machine without modification:

  PAPERPILE_JSON          : path to Paperpile JSON export
  PAPERPILE_PDF_ROOT      : path to local PDF mirror
  RESEARCH_PROJECTS_ROOT  : path to research projects tree (for bib priority);
                            optional — set to empty string to disable priority
  PDF_EXTRACT_MAX_RSS_MB  : own-process RSS cap in MB (default 4096; 0 disables).
                            On breach the current paper is blocklisted and the
                            run ends. Guard added after the 2026-07-24 kernel
                            panic (memory/swap exhaustion).
  PDF_EXTRACT_MIN_FREE_PERCENT : stop the run early if the system-wide free
                            memory percentage (memory_pressure -Q) drops below
                            this between papers (default 15; 0 disables).

A single-instance lock (log/bulk-extract.lock) prevents concurrent runs
(StartOnMount + StartCalendarInterval can otherwise overlap). Papers aborted
for memory are recorded in <cache-root>/bulk-blocklist.json and skipped on
future runs; delete an entry there to retry it.

Usage:
    bulk_extract.py [--max-runtime SECONDS] [--max-papers N] [--no-prioritise]
                    [--paperpile-json PATH] [--pdf-root PATH]
                    [--projects-root PATH] [--per-paper-timeout SEC]
                    [--max-rss-mb MB] [--min-free-percent PCT]
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from pdf_extract import extract


def _env_path(var: str) -> Path | None:
    val = os.environ.get(var)
    if not val:
        return None
    return Path(val).expanduser()


DEFAULT_PAPERPILE = _env_path("PAPERPILE_JSON")
DEFAULT_PDF_ROOT = _env_path("PAPERPILE_PDF_ROOT")
DEFAULT_PROJECTS_ROOT = _env_path("RESEARCH_PROJECTS_ROOT")
DEFAULT_MAX_RUNTIME = 6 * 60 * 60  # 6 hours
DEFAULT_PER_PAPER_TIMEOUT = 120    # 2 min Marker hard cap per paper
# Memory guard (added after the 2026-07-24 kernel panic: this process ballooned
# 81 MB -> 15.1 GB in ~5 min on a large-PDF batch — alone exceeding the Mini's
# 16 GB RAM — and, stacked on a 10 GB llama-server load, exhausted RAM + all
# swap -> watchdog panic). Normal per-paper usage is well under 2 GB, so a
# 4 GB RSS is already pathological.
DEFAULT_MAX_RSS_MB = int(os.environ.get("PDF_EXTRACT_MAX_RSS_MB", "4096"))
DEFAULT_MIN_FREE_PERCENT = int(os.environ.get("PDF_EXTRACT_MIN_FREE_PERCENT", "15"))


class TimeBudgetExceeded(Exception):
    pass


class MemGuardAbort(Exception):
    """Own-process RSS crossed the cap — end the run (a ballooned allocator
    does not reliably return memory to the OS, so continuing is unsafe)."""


def _proc_rss_mb() -> float:
    """Current resident set size of this process in MB (macOS ps reports KB)."""
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return int(out) / 1024.0
    except Exception:
        return 0.0


def _system_free_percent() -> float:
    """System-wide free memory percentage via macOS `memory_pressure -Q`
    (Apple's composite metric — free+inactive alone under-reads a healthy mac)."""
    try:
        out = subprocess.run(
            ["memory_pressure", "-Q"], capture_output=True, text=True, timeout=10,
        ).stdout
        m = re.search(r"free percentage:\s*(\d+)%", out)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return float("inf")  # can't measure — don't block the run on it


def _blocklist_path() -> Path:
    root = os.environ.get("PDF_EXTRACT_CACHE_DIR")
    base = Path(root) if root else Path.home() / ".cache" / "pdf-extract"
    return base / "bulk-blocklist.json"


def _load_blocklist() -> dict:
    path = _blocklist_path()
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _add_to_blocklist(citekey: str, pdf_path: Path, rss_mb: float) -> None:
    path = _blocklist_path()
    entries = _load_blocklist()
    entries[citekey] = {
        "reason": "memory",
        "rss_mb": round(rss_mb),
        "pdf": str(pdf_path),
        "date": time.strftime("%Y-%m-%d"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=1))
    except Exception as e:
        print(f"[memguard] WARNING: could not write blocklist {path}: {e}")


_MEM_BREACH = {"flag": False, "rss_mb": 0.0}


def _start_mem_watchdog(cap_mb: float, interval: float = 5.0) -> threading.Event:
    """Background sampler: if own RSS crosses cap_mb, flag the breach and send
    SIGALRM so the (main-thread-only) handler raises MemGuardAbort."""
    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(interval):
            rss = _proc_rss_mb()
            if rss > cap_mb:
                _MEM_BREACH["flag"] = True
                _MEM_BREACH["rss_mb"] = rss
                os.kill(os.getpid(), signal.SIGALRM)
                return

    threading.Thread(target=_watch, daemon=True, name="mem-watchdog").start()
    return stop


def _acquire_run_lock():
    """Single-instance lock: StartOnMount + StartCalendarInterval can overlap
    (a volume mount during the 02:00 run starts a second instance — twice the
    surya memory). Returns the open handle, or None if another run holds it."""
    lock_path = Path(__file__).resolve().parent.parent / "log" / "bulk-extract.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def resolve_pdf(entry: dict, pdf_root: Path) -> Path | None:
    for att in entry.get("attachments", []) or []:
        if att.get("mimeType") != "application/pdf":
            continue
        filename = att.get("filename", "")
        if filename.startswith("All Papers/"):
            filename = filename[len("All Papers/"):]
        candidate = pdf_root / filename
        if candidate.exists():
            return candidate
    return None


def collect_bib_citekeys(projects_root: Path) -> set[str]:
    """Scan every paper-*/paper/references.bib (and main.bib) under projects_root.

    Returns the set of citekeys that appear in any project bibliography. These
    are the highest-priority papers to extract — they're actively used in
    drafts.
    """
    keys: set[str] = set()
    if not projects_root.exists():
        return keys
    bib_files = list(projects_root.rglob("paper-*/**/references.bib")) + \
                list(projects_root.rglob("paper-*/**/main.bib"))
    import re
    citekey_re = re.compile(r"^\s*@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)
    for bib in bib_files:
        try:
            text = bib.read_text(encoding="utf-8", errors="ignore")
            for m in citekey_re.finditer(text):
                keys.add(m.group(1))
        except Exception:
            continue
    return keys


def is_already_cached(pdf_path: Path, cache_root: Path | None = None) -> bool:
    """Cheap check: does the marker cache already have a doc.json for this PDF?"""
    from pdf_extract import cache as _cache, EXTRACT_VERSION
    return _cache.load(pdf_path, "marker", EXTRACT_VERSION, root=cache_root) is not None


def prioritise(
    library: list[dict], pdf_root: Path, projects_root: Path,
) -> list[tuple[dict, Path]]:
    """Build the extraction queue.

    Order:
      1. Papers cited in any active project's bibliography (hot subset)
      2. Recently-added Paperpile entries (sort by `created` desc)
      3. Long tail
    Returns list of (entry, pdf_path) for papers with available PDFs.
    """
    print(f"[prio] scanning project bibs under {projects_root}")
    hot_keys = collect_bib_citekeys(projects_root)
    print(f"[prio] {len(hot_keys)} citekeys found in project bibliographies")

    candidates: list[tuple[dict, Path]] = []
    hot: list[tuple[dict, Path]] = []
    other: list[tuple[dict, Path]] = []
    for entry in library:
        path = resolve_pdf(entry, pdf_root)
        if path is None:
            continue
        if entry.get("citekey") in hot_keys:
            hot.append((entry, path))
        else:
            other.append((entry, path))

    # Sort 'other' by created timestamp descending (recent first)
    other.sort(key=lambda t: t[0].get("created", 0), reverse=True)
    candidates = hot + other
    print(f"[prio] queue: {len(hot)} hot + {len(other)} long-tail = {len(candidates)} total")
    return candidates


def _alarm_handler(signum, frame):
    if _MEM_BREACH["flag"]:
        raise MemGuardAbort()
    raise TimeBudgetExceeded()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-runtime", type=int, default=DEFAULT_MAX_RUNTIME,
                   help=f"Wall-clock budget in seconds (default {DEFAULT_MAX_RUNTIME})")
    p.add_argument("--max-papers", type=int, default=10000,
                   help="Hard cap on papers per run (safety net)")
    p.add_argument("--per-paper-timeout", type=int, default=DEFAULT_PER_PAPER_TIMEOUT,
                   help="Per-paper timeout in seconds")
    p.add_argument("--no-prioritise", action="store_true",
                   help="Skip project-bib priority pass (random library order)")
    p.add_argument("--paperpile-json", type=Path, default=DEFAULT_PAPERPILE,
                   help="Path to Paperpile JSON export (or set $PAPERPILE_JSON)")
    p.add_argument("--pdf-root", type=Path, default=DEFAULT_PDF_ROOT,
                   help="Path to local PDF mirror (or set $PAPERPILE_PDF_ROOT)")
    p.add_argument("--projects-root", type=Path, default=DEFAULT_PROJECTS_ROOT,
                   help="Path to research projects tree for bib priority "
                        "(or set $RESEARCH_PROJECTS_ROOT). Pass empty / unset to disable.")
    p.add_argument("--cache-root", type=Path, default=None,
                   help="Override cache root (default: $PDF_EXTRACT_CACHE_DIR or auto)")
    p.add_argument("--max-rss-mb", type=int, default=DEFAULT_MAX_RSS_MB,
                   help="Own-process RSS cap in MB; breach blocklists the current "
                        "paper and ends the run (0 disables; or set "
                        "$PDF_EXTRACT_MAX_RSS_MB)")
    p.add_argument("--min-free-percent", type=int, default=DEFAULT_MIN_FREE_PERCENT,
                   help="Stop the run early if system-wide free memory percentage "
                        "(memory_pressure -Q) drops below this between papers "
                        "(0 disables; or set $PDF_EXTRACT_MIN_FREE_PERCENT)")
    args = p.parse_args()

    if args.paperpile_json is None:
        p.error("paperpile JSON path is required: pass --paperpile-json or set $PAPERPILE_JSON")
    if args.pdf_root is None:
        p.error("PDF root is required: pass --pdf-root or set $PAPERPILE_PDF_ROOT")

    if args.cache_root:
        os.environ["PDF_EXTRACT_CACHE_DIR"] = str(args.cache_root)

    run_lock = _acquire_run_lock()
    if run_lock is None:
        print("[lock] another bulk_extract instance is already running — exiting")
        return 0

    print(f"=== bulk_extract starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"  budget: {args.max_runtime}s wall-clock, {args.max_papers} papers max")
    print(f"  per-paper timeout: {args.per_paper_timeout}s")
    print(f"  memguard: RSS cap {args.max_rss_mb} MB, "
          f"min system free {args.min_free_percent}%")

    if not args.paperpile_json.exists():
        print(f"ERROR: paperpile JSON not found: {args.paperpile_json}", file=sys.stderr)
        return 1
    library = json.loads(args.paperpile_json.read_text())
    print(f"  library: {len(library)} entries")

    if args.no_prioritise or args.projects_root is None:
        queue = [(e, p) for e in library if (p := resolve_pdf(e, args.pdf_root)) is not None]
        print(f"  queue: {len(queue)} papers (un-prioritised)")
    else:
        queue = prioritise(library, args.pdf_root, args.projects_root)

    # Filter cached-already
    print(f"[scan] filtering already-cached papers...")
    fresh = [(e, p) for e, p in queue if not is_already_cached(p)]
    print(f"[scan] {len(queue) - len(fresh)} already cached, {len(fresh)} to process")

    # Filter blocklisted papers (previously aborted for memory)
    blocklist = _load_blocklist()
    if blocklist:
        before = len(fresh)
        fresh = [(e, p) for e, p in fresh if e.get("citekey") not in blocklist]
        if before != len(fresh):
            print(f"[memguard] {before - len(fresh)} blocklisted papers skipped "
                  f"(see {_blocklist_path()})")

    if not fresh:
        print("nothing to do — library fully extracted")
        return 0

    # Top-level wall-clock budget
    deadline = time.monotonic() + args.max_runtime
    n_done = 0
    n_failed = 0
    n_timeout = 0
    n_skipped = 0
    n_memguard = 0

    watchdog_stop = None
    if args.max_rss_mb > 0:
        watchdog_stop = _start_mem_watchdog(args.max_rss_mb)

    try:
        for entry, pdf_path in fresh[: args.max_papers]:
            if time.monotonic() >= deadline:
                print(f"[budget] wall-clock exhausted; stopping")
                break

            if args.min_free_percent > 0:
                free_pct = _system_free_percent()
                if free_pct < args.min_free_percent:
                    print(f"[memguard] system free memory {free_pct:.0f}% < "
                          f"{args.min_free_percent}% — stopping run early "
                          f"(deferring to next scheduled window)")
                    break

            citekey = entry.get("citekey", "?")
            # Per-paper timeout via SIGALRM (Python signal handlers run in the
            # main thread only; the mem-watchdog thread never handles signals,
            # it just sends SIGALRM to the process on an RSS breach).
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(args.per_paper_timeout)
            t0 = time.time()
            try:
                doc = extract(pdf_path, backend="marker", use_cache=True)
                n_done += 1
                print(f"[ok]   {citekey:30}  {time.time()-t0:6.1f}s  "
                      f"md={len(doc.markdown):>7}  figs={len(doc.figures):>2}  "
                      f"tabs={len(doc.tables):>2}  refs={len(doc.references):>3}")
            except TimeBudgetExceeded:
                n_timeout += 1
                print(f"[TIMEOUT] {citekey} after {args.per_paper_timeout}s — skipping")
            except MemGuardAbort:
                n_memguard += 1
                _add_to_blocklist(citekey, pdf_path, _MEM_BREACH["rss_mb"])
                print(f"[MEMGUARD] RSS {_MEM_BREACH['rss_mb']:.0f} MB > "
                      f"{args.max_rss_mb} MB cap while extracting {citekey} — "
                      f"blocklisted; ending run (memory is not reliably "
                      f"returned to the OS after a balloon)")
                break
            except Exception as e:
                n_failed += 1
                print(f"[FAIL]  {citekey:30}  {type(e).__name__}: {str(e)[:120]}")
            finally:
                signal.alarm(0)
    except MemGuardAbort:
        # Breach signalled between papers (watchdog race) — still end the run.
        n_memguard += 1
        print(f"[MEMGUARD] RSS {_MEM_BREACH['rss_mb']:.0f} MB > "
              f"{args.max_rss_mb} MB cap between papers — ending run")
    finally:
        if watchdog_stop is not None:
            watchdog_stop.set()

    print(f"=== done: {n_done} extracted, {n_failed} failed, {n_timeout} timeout, "
          f"{n_skipped} skipped, {n_memguard} memguard, "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
