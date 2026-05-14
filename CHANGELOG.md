# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Tests: regression coverage for the `Security Warning` panels printed when `ANIDB_API_KEY` is unset (plaintext UDP fallback) or when ENCRYPT setup fails (e.g. `309 API PASSWORD NOT DEFINED`); existing redaction expanded to `api_key=` alongside `pass=` and `s=`.

### Changed

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
