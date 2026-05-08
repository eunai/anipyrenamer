# Operator runbook — anipyrenamer

Quick reference for running the CLI safely and recovering from common failures. For install, requirements, and overview, see the project [README.md](../README.md).

## Common commands

| Goal | Command |
|------|---------|
| Preview renames only | `anipyrenamer "<path>" --dry-run` |
| Apply with prompts | `anipyrenamer "<path>"` |
| Apply without prompts | `anipyrenamer "<path>" --yes` |
| Plex-style folders | `anipyrenamer "<path>" --plex` (implies `--folder`) |
| Cache-only (no AniDB) | `anipyrenamer "<path>" --offline` |
| Custom cache DB | `anipyrenamer "<path>" --db "C:\path\to\cache.sqlite"` |
| Clear cache for scanned files | `anipyrenamer "<path>" --clear-cache` |
| Clear entire file cache | `anipyrenamer "<path>" --clear-cache-all` |
| MyList after rename | `anipyrenamer "<path>" --mylist` |

Install and developer checks: see the **Development** section in [README.md](../README.md). Full flag reference: `anipyrenamer --help`.

## Safe to rerun

- **`--dry-run`** — No filesystem changes; only shows the plan.
- **`--offline`** — Uses existing SQLite cache only; no UDP to AniDB.
- **Repeat `anipyrenamer` on the same tree** after fixing skips — Items whose destination already exists are skipped; after you remove conflicts or fix paths, a later run can succeed for those items.
- **MyList wizard** — Prompt-driven; declining steps does not rename files.

## Destructive or careful

- **Apply without `--dry-run`** — Moves files; have backups for bulk runs.
- **`--yes`** — Auto-accepts apply.
- **`--clear-cache` / `--clear-cache-all`** — Forces refetch from AniDB on next online run; can increase API load and time.
- **Deleting the cache file** — Same effect as clearing; keep a copy if you need to roll back metadata locally.

## Configuration locations

- **`.env`:** Project root (or parent of package) first, then Windows `%APPDATA%\anipyrenamer\.env` or Unix `~/.config/anipyrenamer/.env`. See [README.md](../README.md) and `.env.example`.
- **Default cache:** Repo: `.cache/anipyrenamer_cache.sqlite` at project root. Else: `.cache/` under the same well-known config directory as `.env`.

## File permissions (recommended)

- **Unix/macOS**: ensure `.env` is not group/other-readable (recommended: `chmod 600 <path-to-.env>`). The CLI emits a warning when it detects a world-readable `.env`.
- **Windows**: the CLI does not modify ACLs. Keep `.env` and the cache DB under your user profile (or ensure NTFS ACLs restrict access) and avoid shared folders for these files.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Fatal error (no safe continuation) |
| 2 | Partial failures (skipped plan items, apply skips, conflicts per policy) |

Treat **2** as “review the log and plan, fix issues, re-run if needed.”

## Failure signatures

| Symptom | Likely cause | What to check |
|---------|--------------|---------------|
| `AniDB request failed after 3 attempts` | UDP loss, firewall, or AniDB down | Network; retry later; try `--offline` if cache populated |
| Login / AUTH errors | Wrong credentials or banned client | `.env` vs `.env.example`; AniDB client name/version |
| No rename plan / many SKIP rows | Missing FILE in AniDB or empty cache offline | Run online once; verify ED2K and file size |
| “Using local cache” but wrong title | Stale or bad cache row | `--refresh-cache` or `--clear-cache` for those files |
| Permission denied on move | File open elsewhere; ACL | Close players; run with access to paths |
| Exit 2 after apply | Some destinations existed or moves failed | Preview warnings; `--on-conflict` / dedupe options |
| Wizard / MyList issues | No `fid` or session | Files must be known to AniDB; run a successful online rename or lookup first |

## Observability

- **Normal:** Rich progress, cache vs fetch indicators, Warnings panel for conflicts.
- **Hashing phase:** One per-file progress row shows the file **currently** being hashed; after its hash completes, cache/AniDB lookup runs before the next file. Large files may use a faster read path when mmap is available.
- **`--log-level` / `--log-file`:** Standard-library logging for the `anipyrenamer.*` namespace (`DEBUG`/`INFO`/`WARNING`/`ERROR`, default warning on stderr); optional `--log-file` mirrors the same structured lines as UTF-8 append. Independent of Rich; does not weaken redaction.
- **`--debug`:** Extra diagnostics; passwords and session keys must not appear in logs. AniDB UDP debug lines redact `pass=` and `s=` (session) in outbound and inbound previews (`src/anipyrenamer/anidb.py`). If you see a warning that received data may be truncated, the UDP reply was large and metadata may be incomplete.
- **Plan errors (`escapes destination root`):** The rename plan refused a path that would resolve outside the allowed directory (custom destination, folder layout, or source parent). Adjust templates or paths.
- **Environment loading:** Variables from `.env` load when you run the `anipyrenamer` command (`anipyrenamer.cli:main`), not when importing the package as a library.

For offline retries, warm the cache with a successful online run, then use `--offline` for subsequent passes.
