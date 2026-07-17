# anipyrenamer

![version: 2.3.0](https://img.shields.io/badge/version-2.3.0-blue?style=flat-square)

`anipyrenamer` is a command-line tool that renames anime files using ED2K hash lookups from AniDB, with local SQLite caching for faster reruns and offline support.

It fits alongside local media libraries (Plex/Jellyfin folders, plain disk collections): you point it at files or folders, it resolves titles and episode metadata from AniDB, then renames in place or into a destination tree.

## Requirements

- **Python 3.13+**
- An AniDB account and UDP API client registration (for online lookup). You can use `--offline` when the SQLite cache already has the rows you need.

## Install

```bash
# From the repository root (the directory that contains pyproject.toml).

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
cp .env.example .env
# edit .env — never commit secrets
```

For a minimal install without dev dependencies: `pip install -e .`

Optional helper scripts are in `scripts/` (`install.sh` / `install.bat`, `install-dev.*`, `setup-venv.*`).

## Configuration

Put credentials and client strings in `.env` (see `.env.example`). Required for online mode:

- `ANIDB_USERNAME`, `ANIDB_PASSWORD`, `ANIDB_UDP_CLIENT`, `ANIDB_UDP_CLIENTVER`, `ANIDB_LOCAL_PORT`

Optional:

- `ANIDB_API_KEY` (when UDP encryption is enabled)

**Where `.env` is loaded:** project/package directory and parents first, then a well-known config path: Windows `%APPDATA%\anipyrenamer\.env`, Linux/macOS `~/.config/anipyrenamer/.env`.

**Cache:** When a parent directory contains `pyproject.toml`, the default database is project root `.cache/anipyrenamer_cache.sqlite`. Otherwise it lives under the same config directory as `.env`. Override with `--db`.

## Usage

```bash
anipyrenamer <path> [paths...]
```

Examples:

```bash
anipyrenamer "C:\Videos\Anime" --dry-run
anipyrenamer "C:\Videos\Anime"
anipyrenamer "C:\Videos\Anime" --yes
anipyrenamer "C:\Videos\Anime" -t "%title% S01E%epno%%ext%" -d "C:\Renamed"
anipyrenamer "C:\Videos\Anime" --mylist
```

Use `anipyrenamer --help` for all options.

### Confirmation prompts

The rename-apply confirmation uses `Prompt text (Y/n/a):` — `Y` (default), `n`, or `a` (yes to all remaining).

The `--mylist` wizard uses `Prompt text (Y/n):` — `Y` (default) or `n`, with **no** "yes to all": every MyList prompt is an independent yes/no, and the final `Apply MyList updates?` prompt is the only confirmation that writes to your AniDB MyList.

### MyList storage states

When you run `--mylist` and choose to set storage, the menu is 0-indexed and the number you type **is** the AniDB MyList state code that gets saved:

| Key | Meaning |
|-----|---------|
| `0` | Unknown/None |
| `1` | Internal (on hdd) |
| `2` | External (on cd/dvd) |
| `3` | Deleted |
| `4` | Remote (NAS/cloud) |
| `5` | Exit (abort without writing) |

Only the state code is written; no free-text storage name is sent. Verified on 2026.06.26 against the live AniDB MyList UI. Legacy AniDB clients that label `4` as "shared" or `5` as "release" are out of date — AniDB repurposed those codes after retiring P2P sharing.

## Platforms

Developed and tested on **Windows** and **Unix-like** systems (Linux, macOS). Paths in examples use Windows style where helpful; adjust for your OS.

## Troubleshooting

See [docs/runbook.md](docs/runbook.md) for exit codes, common failures, and safe reruns.

## Contributing and issues

Report bugs and request features via the project issue tracker on [GitHub](https://github.com/eunai/anipyrenamer/issues).

## Development

Parts of this codebase were written or refactored with **AI-assisted coding tools**, alongside manual design, testing, and review. Human maintainers remain responsible for behavior and releases.

## License

Distributed under the [MIT License](LICENSE). Dependency licenses vary; review them when preparing redistributions (wheel, bundle, installer).
