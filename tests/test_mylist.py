"""Tests for optional advanced MyList wizard flow."""

from __future__ import annotations

import pytest
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


def test_mylist_wizard_first_prompt_is_storage(monkeypatch) -> None:
    """R1: --mylist implies intent; the wizard never re-asks 'update MyList?'.

    The first prompt is 'set storage?' and no 'update MyList?' prompt is ever asked.
    """
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    asked: list[str] = []

    def _confirm(message: str) -> str:
        asked.append(message)
        return "n"

    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    run_mylist_wizard(
        console=console,
        client=client,
        file_infos=[_file_info(42)],
        confirm=_confirm,
    )
    assert asked, "the wizard must ask at least one prompt"
    assert asked[0] == "Would you like to set storage?"
    assert all("update MyList" not in message for message in asked)


def test_mylist_wizard_no_session_skips_immediately() -> None:
    """R6: with no AniDB session, skip before any prompt; skipped == len(entries)."""
    console = Console(record=True, no_color=True)
    confirm_calls = {"count": 0}

    def _confirm(_message: str) -> str:
        confirm_calls["count"] += 1
        return "y"

    result = run_mylist_wizard(
        console=console,
        client=None,
        file_infos=[_file_info(10), _file_info(11)],
        confirm=_confirm,
    )
    assert confirm_calls["count"] == 0
    assert result.attempted is False
    assert result.skipped == 2


def test_mylist_wizard_applies_with_storage_and_watched(monkeypatch) -> None:
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    # Prompts (no top-level "update MyList?"): storage? / add? / watched? / apply?.
    answers = iter(["y", "y", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")  # 0-indexed: key 2 == state 2

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
        (100, True, 2, None, True),
        (101, True, 2, None, True),
    ]


def test_mylist_wizard_omits_freetext_storage(monkeypatch) -> None:
    """R5: applying storage sends only the state code; the free-text storage= field is omitted."""
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    run_mylist_wizard(
        console=console,
        client=client,
        file_infos=[_file_info(300)],
        confirm=lambda _message: "y",
    )
    assert client.calls, "a storage selection should produce a MyList call"
    # tuple is (fid, add_to_mylist, state, storage, viewed)
    assert client.calls[0][2] == 1  # state code persisted
    assert client.calls[0][3] is None  # free-text storage label NOT sent


def test_mylist_wizard_storage_key_maps_to_state(monkeypatch) -> None:
    """R4: storage menu is 0-indexed; the typed key equals the AniDB MyList state code."""
    for key, expected_state in (("1", 1), ("4", 4)):
        client = _FakeMyListClient()
        monkeypatch.setattr("builtins.input", lambda _prompt, _k=key: _k)
        run_mylist_wizard(
            console=Console(record=True, no_color=True),
            client=client,
            file_infos=[_file_info(200)],
            confirm=lambda _message: "y",
        )
        assert client.calls, f"key {key} should produce a MyList call"
        # tuple is (fid, add_to_mylist, state, storage, viewed)
        assert client.calls[0][2] == expected_state


def test_mylist_wizard_storage_exit_aborts(monkeypatch) -> None:
    """AC #11: selecting Exit (new 0-indexed key 5) aborts cleanly before any write."""
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    monkeypatch.setattr("builtins.input", lambda _prompt: "5")

    result = run_mylist_wizard(
        console=console,
        client=client,
        file_infos=[_file_info(9)],
        confirm=lambda _message: "y",
    )
    assert result.attempted is False
    assert client.calls == []
    assert "aborted at storage step" in console.export_text()


def test_mylist_wizard_item_progress_fires_per_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: MyList applies one API call per fid; progress hook matches that granularity.

    Driven by explicit per-prompt ``"y"`` answers (no yes-to-all): each prompt is an
    independent yes.
    """
    events: list[tuple[int, int, str]] = []

    def _progress(current: int, total: int, phase: str) -> None:
        events.append((current, total, phase))

    client = _FakeMyListClient()
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    run_mylist_wizard(
        console=Console(record=True, no_color=True),
        client=client,
        file_infos=[_file_info(1), _file_info(2)],
        confirm=lambda _message: "y",
        item_progress=_progress,
    )
    assert events == [
        (1, 2, "start"),
        (1, 2, "end"),
        (2, 2, "start"),
        (2, 2, "end"),
    ]


def test_mylist_wizard_no_yes_to_all_cascade(monkeypatch) -> None:
    """R2: an early 'yes' must not auto-confirm later prompts.

    Answer yes to storage/add/watched but no to the final apply: nothing is sent.
    Under any residual cascade the early 'yes' would auto-confirm apply.
    """
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    answers = iter(["y", "y", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    result = run_mylist_wizard(
        console=console,
        client=client,
        file_infos=[_file_info(7)],
        confirm=lambda _message: next(answers),
    )
    assert result.attempted is False
    assert client.calls == []


def test_mylist_wizard_a_reply_is_not_yes_to_all(monkeypatch) -> None:
    """R2: 'a' is inert at the wizard boundary (no yes-to-all cascade).

    If the injected confirm ever yields 'a', the wizard treats it as not-yes for
    every prompt — including apply — so nothing is written to AniDB.
    """
    console = Console(record=True, no_color=True)
    client = _FakeMyListClient()
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    result = run_mylist_wizard(
        console=console,
        client=client,
        file_infos=[_file_info(8)],
        confirm=lambda _message: "a",
    )
    assert result.attempted is False
    assert client.calls == []
