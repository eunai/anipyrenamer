# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository `LICENSE`: **MIT**.

### Fixed

- Environment variables from `.env` load only when the CLI entry point runs, not when importing the CLI module. SQLite schema migrations validate dynamic column names before altering tables.

### Changed

- Operator runbook ships at `docs/runbook.md` on the published repository.
- ED2K hashing uses memory-mapped reads for large files (with automatic fallback) to reduce overhead on fast storage.
- README and operator runbook describe sequential per-file hashing (hash, then cache/lookup, then the next file) and how large files use mmap with read fallback.

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
- Legacy implementation review document (superseded by implementation status and the spec).

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
