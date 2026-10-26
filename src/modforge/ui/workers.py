"""Small Qt thread-pool wrapper for blocking filesystem work."""
from PySide6.QtCore import QObject, QRunnable, Signal


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class FunctionWorker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.finished.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(str(exc))
