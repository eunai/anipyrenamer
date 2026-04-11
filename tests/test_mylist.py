"""Tests for Phase 2 MyList wizard flow."""

from __future__ import annotations

from rich.console import Console

from anipyrenamer.models import FileInfo
from anipyrenamer.mylist import run_mylist_wizard


class _FakeMyListClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, bool, int | None, str | None, bool | None]] = []

    def mylist_add_or_update_by_fid(
        self,
        fid: int,
        *,
        add_to_mylist: bool,
        state: int | None = None,
        storage: str | None = None,
        viewed: bool | None = None,
    ) -> tuple[bool, str]:
        self.calls.append((fid, add_to_mylist, state, storage, viewed))
        return (True, "ok")


def _file_info(fid: int) -> FileInfo:
    return FileInfo(
        fid=fid,
        aid=1,
        eid=2,
        gid=3,
        size=4,
        ed2k="a" * 32,
        quality="high",
        source="TV",
    )


def test_mylist_wizard_skips_when_no_fid() -> None:
    console = Console(record=True, no_color=True)
    result = run_mylist_wizard(
        console=console,
        client=_FakeMyListClient(),
        file_infos=[_file_info(0)],
        confirm=lambda _message: "y",
    )
    assert result.attempted is False
    assert result.skipped == 1


def test_mylist_wizard_applies_with_storage_and_watched(monkeypatch) -> None:
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    answers = iter(["y", "y", "y", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    result = run_mylist_wizard(
        console=console,
        client=client,
        file_infos=[_file_info(100), _file_info(100), _file_info(101)],
        confirm=lambda _message: next(answers),
    )
    assert result.attempted is True
    assert result.applied == 2
    assert result.failed == 0
    assert client.calls == [
        (100, True, 1, "Internal HDD", True),
        (101, True, 1, "Internal HDD", True),
    ]


def test_mylist_wizard_all_remaining_shortcut(monkeypatch) -> None:
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    answers = iter(["a", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    result = run_mylist_wizard(
        console=console,
        client=client,
        file_infos=[_file_info(55)],
        confirm=lambda _message: next(answers),
    )
    assert result.attempted is True
    assert result.applied == 1
    assert client.calls == [(55, True, 0, "Unknown", True)]
