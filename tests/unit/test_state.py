import pytest

from app.domain.files.entities import FileStatus
from app.domain.files.exceptions import InvalidStateTransition
from app.domain.files.state import can_transition, transition


def test_available_can_be_deleted() -> None:
    assert can_transition(FileStatus.AVAILABLE, FileStatus.DELETED)
    assert transition(FileStatus.AVAILABLE, FileStatus.DELETED) is FileStatus.DELETED


def test_deleted_is_terminal() -> None:
    assert not can_transition(FileStatus.DELETED, FileStatus.AVAILABLE)
    with pytest.raises(InvalidStateTransition) as exc:
        transition(FileStatus.DELETED, FileStatus.AVAILABLE)
    assert exc.value.code == "INVALID_STATE_TRANSITION"
    assert exc.value.context["current"] is FileStatus.DELETED


def test_self_transition_not_allowed() -> None:
    assert not can_transition(FileStatus.AVAILABLE, FileStatus.AVAILABLE)
