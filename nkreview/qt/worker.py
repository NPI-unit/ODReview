"""Cellpose in the background: one thread works through the folder, the image
on screen first. Results land in the .nk_cache, the window is told per image."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..segment import CellposeParams, cellpose_for_image, has_cellpose_cache
from ..state import load_image


class CellposeWorker(QObject):
    started = Signal(str)              # image path
    finished = Signal(str)
    failed = Signal(str, str)
    idle = Signal()

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._queue: list[tuple[Path, Path, CellposeParams, bool]] = []
        self.stain_vectors: list | None = None
        self._stop = False
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._run)
        self._thread.start()

    # Called from the GUI thread ─────────────────────────────
    def set_queue(self, folder: Path, images: list[Path], params: CellposeParams) -> None:
        with self._lock:
            self._queue = [(folder, p, params, False) for p in images]
        self._wake.set()

    def prioritize(self, image: Path) -> None:
        with self._lock:
            for i, item in enumerate(self._queue):
                if item[1] == image:
                    self._queue.insert(0, self._queue.pop(i))
                    break

    def force(self, folder: Path, image: Path, params: CellposeParams) -> None:
        with self._lock:
            self._queue = [q for q in self._queue if q[1] != image]
            self._queue.insert(0, (folder, image, params, True))
        self._wake.set()

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def shutdown(self) -> None:
        self._stop = True
        self._wake.set()
        self._thread.quit()
        self._thread.wait(3000)

    # Worker thread ──────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop:
            with self._lock:
                item = self._queue.pop(0) if self._queue else None
            if item is None:
                self.idle.emit()
                self._wake.wait()
                self._wake.clear()
                continue
            folder, image, params, force = item
            if not force and has_cellpose_cache(folder, image, params):
                self.finished.emit(str(image))
                continue
            self.started.emit(str(image))
            try:
                cellpose_for_image(folder, image, load_image(image), params, force=force,
                                   stain_vectors=self.stain_vectors)
                self.finished.emit(str(image))
            except Exception as e:  # noqa: BLE001 — one bad image must not stop the batch
                traceback.print_exc()
                self.failed.emit(str(image), f"{type(e).__name__}: {e}")
