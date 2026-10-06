"""Run a tasks.Task in Qt: a worker QThread, a modal progress dialog, and
progress/result delivered to the main thread through signals.

The work function never touches a widget (tasks.py contract); everything the
dialog shows arrives via queued signals, which Qt delivers on the GUI thread.
"""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QProgressDialog, QWidget

from ..tasks import CancelFlag, Task


class _Worker(QObject):
    progress = Signal(object, object)       # value, text
    finished = Signal(object, object)       # result, error

    def __init__(self, task: Task, cancel: CancelFlag):
        super().__init__()
        self._task, self._cancel = task, cancel

    def run(self) -> None:
        try:
            result = self._task.work(lambda v, text=None: self.progress.emit(v, text),
                                     self._cancel)
        except BaseException as e:  # noqa: BLE001 — re-raised on the GUI thread
            self.finished.emit(None, e)
            return
        self.finished.emit(result, None)


class _Relay(QObject):
    """Lives on the GUI thread. Signals from the worker thread connected to its
    slots are queued by Qt and run here — plain Python callables connected
    directly would run on the worker thread and touch widgets from there."""

    def __init__(self, on_progress, on_finished):
        super().__init__()
        self._p, self._f = on_progress, on_finished

    @Slot(object, object)
    def progress(self, value, text):
        self._p(value, text)

    @Slot(object, object)
    def finished(self, result, error):
        self._f(result, error)


def run_task_modal(parent: QWidget, task: Task, title: str, label: str,
                   maximum: int = 0, cancel_text: str | None = None):
    """Run `task` behind a modal progress dialog and return its result
    (re-raising its exception). maximum=0 shows a busy indicator. The window
    keeps repainting meanwhile; only the dialog accepts input."""
    cancel = CancelFlag()
    dlg = QProgressDialog(label, cancel_text, 0, maximum, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(300)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    if cancel_text is None:
        dlg.setCancelButton(None)
    else:
        dlg.canceled.connect(cancel.cancel)

    thread = QThread(parent)
    worker = _Worker(task, cancel)
    worker.moveToThread(thread)
    loop = QEventLoop()
    out: dict = {}

    def on_progress(value, text):
        if value is not None and maximum:
            dlg.setValue(int(value))
        if text:
            dlg.setLabelText(str(text))

    def on_finished(result, error):
        out["result"], out["error"] = result, error
        thread.quit()

    relay = _Relay(on_progress, on_finished)
    worker.progress.connect(relay.progress, Qt.QueuedConnection)
    worker.finished.connect(relay.finished, Qt.QueuedConnection)
    thread.started.connect(worker.run)
    thread.finished.connect(loop.quit)
    thread.start()
    loop.exec()
    thread.wait()
    dlg.close()
    worker.deleteLater(); thread.deleteLater()
    if out.get("error") is not None:
        raise out["error"]
    return out.get("result")


_RUNNING: set = set()


def shutdown(timeout_ms: int = 3000) -> None:
    """Cancel every background task and wait for its thread — at quit, so no
    QThread is destroyed while it still runs."""
    for rt in list(_RUNNING):
        rt.cancel()
    for rt in list(_RUNNING):
        rt.thread.quit()
        rt.thread.wait(timeout_ms)
    _RUNNING.clear()


class RunningTask:
    """Handle for start_task: cancel() asks the work to stop (it checks
    `is_cancelled` between steps); the thread cleans up after itself."""

    def __init__(self, thread: QThread, worker: _Worker, relay: _Relay, cancel: CancelFlag):
        self.thread, self.worker, self.relay, self._cancel = thread, worker, relay, cancel

    def cancel(self) -> None:
        self._cancel.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancel()


def start_task(parent: QWidget, task: Task, on_progress, on_done) -> RunningTask:
    """Run `task` on a QThread without blocking; `on_progress(value, text)` and
    `on_done(result, error)` are called on the GUI thread.

    Teardown happens in one place, on the GUI thread, in a fixed order: the
    thread is asked to quit and waited for, the objects are released, and only
    then is `on_done` called. (Releasing them from signals while the slot that
    delivers the result was still running crashed or deadlocked.)"""
    cancel = CancelFlag()
    thread = QThread()
    worker = _Worker(task, cancel)
    worker.moveToThread(thread)
    holder: dict = {}

    def deliver(result, error):
        thread.quit()
        thread.wait()
        _RUNNING.discard(holder.get("rt"))
        worker.deleteLater()
        relay.deleteLater()
        holder.clear()                      # drops the last Python reference to the thread
        on_done(result, error)

    def finished(result, error):
        QTimer.singleShot(0, lambda: deliver(result, error))

    relay = _Relay(on_progress, finished)
    relay.setParent(parent)
    worker.progress.connect(relay.progress, Qt.QueuedConnection)
    worker.finished.connect(relay.finished, Qt.QueuedConnection)
    thread.started.connect(worker.run)
    holder["thread"] = thread               # alive until deliver() ran
    rt = RunningTask(thread, worker, relay, cancel)
    holder["rt"] = rt
    _RUNNING.add(rt)
    thread.start()
    return rt
