"""File lifecycle state machine.

Small on purpose: a file is either available or deleted. Encoding the allowed
transitions here means new states (e.g. ``quarantined`` after a virus scan) have a
single place to be added and validated.
"""

from __future__ import annotations

from app.domain.files.entities import FileStatus
from app.domain.files.exceptions import InvalidStateTransition

_TRANSITIONS: dict[FileStatus, frozenset[FileStatus]] = {
    FileStatus.AVAILABLE: frozenset({FileStatus.DELETED}),
    FileStatus.DELETED: frozenset(),
}


def can_transition(current: FileStatus, target: FileStatus) -> bool:
    return target in _TRANSITIONS[current]


def transition(current: FileStatus, target: FileStatus) -> FileStatus:
    if not can_transition(current, target):
        raise InvalidStateTransition(
            f"cannot move file from {current.value} to {target.value}", current=current, target=target
        )
    return target
