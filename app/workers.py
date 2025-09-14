from PySide6.QtCore import QObject, Signal, Slot
from app.bazaar import Bazaar

class ScanWorker(QObject):
    started = Signal()
    progress = Signal(str)
    finished = Signal(list, bool)

    @Slot()
    def run(self):
        self.started.emit()
        self.progress.emit("Veriler çekiliyor...")
        bz = Bazaar()
        try:
            rows = bz.analyze_bazaar()
            self.progress.emit(f"{len(rows)} ürün alındı.")
            self.finished.emit(rows, True)
        except Exception as e:
            self.progress.emit(f"Hata: {e}")
            self.finished.emit([], False)