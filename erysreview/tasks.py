"""Background work, described without any UI toolkit.

A `Task` is the work itself: a function that gets `progress(value, text=None)`
and `is_cancelled()` and returns a result. It must not touch any UI object —
it runs on a worker thread. How it is run is the front end's business:

  - Tk today: a modal busy dialog pumps the event loop until the worker is
    done (dialogs.run_in_thread_pumping / the batch dialog's queue poll).
  - Qt next: a QThread whose signals carry `progress` to the main thread.
  - Tests and scripts: `run_sync`, on the calling thread.

Keeping the work in this shape is what lets both front ends share it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")

Progress = Callable[..., None]          # progress(value, text=None)
IsCancelled = Callable[[], bool]


@dataclass
class Task(Generic[T]):
    work: Callable[[Progress, IsCancelled], T]
    title: str = ""
    cancellable: bool = False


def simple(fn: Callable[[], T], title: str = "") -> Task[T]:
    """A task that reports no progress and cannot be cancelled midway."""
    return Task(work=lambda _progress, _is_cancelled: fn(), title=title)


class CancelFlag:
    """Thread-safe 'please stop' switch handed to a task as `is_cancelled`."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def __call__(self) -> bool:
        return self._event.is_set()


def run_sync(task: Task[T], progress: Progress | None = None,
             is_cancelled: IsCancelled | None = None) -> T:
    return task.work(progress or (lambda *a, **k: None), is_cancelled or (lambda: False))


def run_in_thread(task: Task[T], progress: Progress, is_cancelled: IsCancelled,
                  on_done: Callable[[T | None, BaseException | None], Any]) -> threading.Thread:
    """Start `task` on a daemon thread; `on_done(result, error)` is called on
    THAT thread — a front end must hop back to its own main thread itself."""
    def _worker() -> None:
        try:
            result = task.work(progress, is_cancelled)
        except BaseException as e:  # noqa: BLE001 — handed to on_done
            on_done(None, e)
            return
        on_done(result, None)

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    return th
