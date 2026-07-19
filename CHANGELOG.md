# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.4.1] - 2026-07-19

### Fixed

- The MyList update step no longer crashes with a raw error when AniDB temporarily bans the client for sending requests too quickly. Such a ban is now detected and reported clearly — including AniDB's stated reason, such as `Flooding` — so the MyList step stops immediately instead of pressing on into a stack trace, and the run finishes with its normal summary and a non-zero exit code.
- anipyrenamer now paces its AniDB requests more conservatively, in line with AniDB's documented rate limits, to reduce the chance of triggering that ban during larger MyList updates. As a result, the AniDB lookup and MyList steps are somewhat slower.

## [2.4.0] - 2026-07-18

### Added

- On Windows, apply now automatically retries a rename that fails because the destination file is briefly held open by another program — a media player, antivirus scan, or search indexer — up to two retries with a short backoff before giving up. This clears the common case where a file is only momentarily locked, without needing to re-run the command by hand. (#55)

### Fixed

- Renaming a file no longer crashes the whole run when the destination folder can't be created or the file itself can't be moved — for example, a file locked by another program on Windows. Previously this printed a raw error and stopped partway through the batch; the tool now skips just that one file, reports it as `apply failed` in the summary, and continues with the rest, still exiting with the same code (`2`) used for other partially-completed runs. In the rare case where the destination file was partially written before the failure, nothing is cleaned up automatically — the output flags that item for manual review before you re-run. (#54)

## [2.3.0] - 2026-07-17

### Changed

- Redesigned the terminal output into a **Quiet Ledger**: a single running log with one line per stage (`discover` / `hash+look` / `plan` / `apply` / `mylist`) that ends with a summary recapping what happened, the destination folder, and a plain-language explanation of the exit code. This replaces the old table preview and bold section headers with a quieter, more scannable stream; noisy per-file progress is now collapsed into simple counts (e.g. `12 cached · 3 fetched · 1 no match`). Every flag, prompt, and exit code behaves exactly as before — `--preview-format table` still selects the human-readable view (just redesigned) and `--preview-format json` is unchanged. (#51)

## [2.2.0] - 2026-07-12

### Added

- Added `anipyrenamer --doctor`, a standalone preflight report for `.env` discovery, AniDB credential presence, encryption posture, cache readiness, and AniDB UDP transport reachability. It is diagnose-only: it does not scan, hash, rename, update MyList, authenticate, or modify the cache. Output redacts values; `--offline` skips only the network check. (#33)

### Fixed

- The warning about a world-readable `.env` now checks the resolved path returned by dotenv discovery, including a parent-directory `.env`, instead of always checking a `.env` in the current directory. Previously the warning could inspect the wrong file — or none — and miss an exposed `.env` in use. The well-known config-directory path continues to be checked separately. The warning names paths only, never file contents. (#39)

## [2.1.0] - 2026-07-05

### Changed

- Cached AniDB file lookups no longer expire automatically: once a file is looked up, its entry stays usable until you explicitly refresh or clear it — `--refresh-cache`, `--clear-cache`/`--clear-cache-all`, or an automatic repair when a cached title looks wrong and AniDB is reachable. Offline runs (`--offline`) now use older cache entries instead of skipping them; rescans of an already-known library no longer silently re-hit AniDB, so use `--refresh-cache` when you want to pick up title or group corrections.

## [2.0.1] - 2026-06-28

### Fixed

- Windows: a folder path ending in a backslash (for example from PowerShell tab-completion) is no longer mis-read as empty — the stray shell quote is stripped so the folder is scanned. (#23)

## [2.0.0] - 2026-06-27

_2.0.0 supersedes the abandoned 1.2.0 release. It re-baselines the 1.1.0 line; the 1.2.0-only features listed under Removed are intentionally not carried forward._

### Changed

- The `--mylist` wizard is now explicit and safer: it no longer asks the redundant "update MyList?" prompt, prompts are `(Y/n)` (no "yes to all"), and the storage menu is 0-indexed where the number you type is the AniDB MyList state code (`0` Unknown/None, `1` Internal, `2` External, `3` Deleted, `4` Remote, `5` Exit). Choosing a storage option now saves only the state code; no free-text storage name is sent. With no AniDB session, MyList is skipped immediately. The rename-apply confirmation is unchanged.

### Removed

- The `a` ("yes to all") answer is no longer accepted in the `--mylist` wizard; the final "Apply MyList updates?" prompt is the only confirmation that writes to AniDB.
- Superseding 1.2.0: the interactive-start **options checklist** (the bare-invocation scan-path prompt and beginner multi-select) and the **rename-plan preview redesign** (per-column separators; sectioned Folders / Files output; tree branches; green diverging-segment highlight) that shipped in 1.2.0 are not present in 2.0.0.

## [1.2.0] - 2026-06-04

### Added

- CLI: interactive start now offers an options checklist. Running `anipyrenamer` without a path in a terminal shows the scan-path prompt and then a multi-select of beginner-friendly options: Preview only (`--dry-run`, checked by default, with a notice that nothing is renamed until you turn it off), Rename series folders (`--folder`) with Plex / HAMA tags (`--plex`) nested beneath it, and Offline — cache only (`--offline`). Confirming Plex without Folder is rejected so the unsupported combination cannot run; the selections drive the same behavior as the equivalent flags, and Ctrl-C / Esc cancels cleanly with exit code `0`.
- Tests: regression coverage for the `Security Warning` panels printed when `ANIDB_API_KEY` is unset (plaintext UDP fallback) or when ENCRYPT setup fails (e.g. `309 API PASSWORD NOT DEFINED`); existing redaction expanded to `api_key=` alongside `pass=` and `s=`.

### Changed

- Documentation: clarified the published issue-reporting policy. The public GitHub issue tracker remains the place to report bugs and feature requests, while maintainers may mirror accepted work into a separate development tracker.

- CLI: Rename plan preview tables now draw `│` between every column (`Current`, `New`, `Type` in Folders; `Current`, `New` in Files and Files (skipped)) for clearer visual scanning.
- CLI: Rename plan preview now renders flat `Folders`, `Files`, and conditional `Files (skipped)` sections with a factored plan-root header per filesystem root. The `Files` section uses Unicode tree branches under each source folder and highlights diverging destination path segments in green, replacing the single flat preview table.
- Internal documentation: tightened the AniDB UDP `ENCRYPT` contract description so it matches the shipped 1.1.0 behavior (CLI warning + plaintext UDP fallback when `ANIDB_API_KEY` is absent or ENCRYPT setup fails). No operator-visible behavior change.

## [1.1.1] - 2026-05-08

### Fixed

- CLI: `.env` is loaded by walking upward from the **current working directory** first, then the well-known user config path (`%APPDATA%\anipyrenamer\.env` on Windows, `~/.config/anipyrenamer/.env` on Unix). Fixes incorrect loading from an unrelated editable-install source tree.

### Added

- Documentation: `--plex --mylist` usage demo GIF in `README.public.md`.

## [1.1.0] - 2026-05-07

### Security

- AniDB UDP `ENCRYPT` / AES session support when `ANIDB_API_KEY` is set; warning + plaintext fallback when it is missing or ENCRYPT setup fails.
- Unix: warn when `.env` is group/other-readable; set cache DB file mode to `0o600` after creation (best-effort).

### Added

- `--log-level` / `--log-file` structured diagnostics (stderr + optional UTF-8 file).

### Changed

- Per-file hashing progress row shows basename only.
- Removed inert `--batch-size` option.

## [1.0.8] - 2026-04-30

### Changed

- **Hashing:** ED2K runs **sequentially on the main thread** (one file at a time: hash, then cache/lookup, then next). The `--clear-cache` prehash path is sequential. Improves Ctrl+C responsiveness compared with multi-threaded hashing in earlier 1.0.x builds.

## [1.0.7] - 2026-04-11

### Security

- Importing `anipyrenamer.cli` no longer loads `.env`; loading runs from `main()` only. Cache migrations validate `ALTER TABLE` column names.

## [1.0.6] - 2026-04-11

### Security

- Larger UDP receive buffer with a clear warning when a datagram fills the buffer. Safer parsing of integer and string fields in AniDB responses. Rename planning rejects output paths that would escape the configured root (destination, folder, or in-place modes).

## [1.0.5] - 2026-04-11

### Security

- Debug logging masks passwords and session keys in AniDB UDP traces.

## [1.0.4] - 2026-04-07

### Added

- Operator runbook for commands, safe reruns, and troubleshooting.
- Optional well-known `.env` path for global installs (Windows `%APPDATA%`, Unix `~/.config`).
- Tests for well-known config path helpers and destination deduplication used by `--on-conflict=suffix`.

### Changed

- README and changelog follow Keep a Changelog 1.1.0-style discipline.
- Reliability and runbook pointers consolidated in the main technical specification.

### Removed

- Standalone architecture flow document (content folded into the main specification).
- Legacy implementation review document (superseded by the main specification §9.1).

### Fixed

- Suffix deduplication path comparisons hardened for edge cases.
- Windows apply no longer aborts when run from inside a folder being renamed, if empty-folder cleanup fails.

## [1.0.2] - 2026-03-12

### Fixed

- Default SQLite cache uses project root or a config-directory cache, not the current working directory, so the cache is not created inside the tree being renamed.

## [1.0.1] - 2026-03-12

### Changed

- Default cache filename is `.cache/anipyrenamer_cache.sqlite` (hidden-style name under `.cache/`) instead of a dotfile at the old location.

## [1.0.0] - 2026-03-12

### Added

- MyList wizard via `--mylist` and related tests.

### Changed

- Interactive confirmations standardized to `(Y/n/a)` with Enter defaulting to yes.
- MyList runs after the main pipeline when requested.

### Fixed

- Typing improvements for ED2K hashing.

### Security

- Credentials remain in `.env` only; no secrets committed.

## [0.2.0] - 2026-02-27

### Added

- Conflict policy flags, preview format selection, and cache refresh controls.

### Changed

- Improved conflict detection and rename deduplication.

## [0.1.0] - 2026-02-27

### Added

- Initial CLI pipeline: discovery, ED2K, AniDB lookup, cache, plan, preview, and apply.
