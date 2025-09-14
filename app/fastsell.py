# fast.py
# JITTER YOK — Bekleme süresi sadece data/config.json → fastsell.interval'dan okunur.
# Kullanıcı config.json'ı manuel değiştirdiğinde, worker her adımda dosyayı yeniden okuyup uygular.

import json
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot

CONFIG_PATH = Path("data/config.json")
COORDS_PATH_DEFAULT = "data/coordinates.json"

def _safe_read_interval() -> float:
    """
    data/config.json içinden fastsell.interval'ı güvenle okur.
    Bulunamazsa veya hatalıysa 0.0 döner (bekleme yok).
    {
      "fastsell": { "interval": 0.3 }
    }
    """
    try:
        if not CONFIG_PATH.exists():
            return 0.0
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        fs = cfg.get("fastsell", {}) or {}
        val = float(fs.get("interval", 0.0) or 0.0)
        if val < 0:
            val = 0.0
        return val
    except Exception:
        return 0.0


class FastSellWorker(QObject):
    started = Signal()
    progress = Signal(str)
    finished = Signal(bool)

    def __init__(self, coords_path: str = COORDS_PATH_DEFAULT):
        super().__init__()
        self.coords_path = coords_path
        self._abort = False

    @Slot()
    def run(self):
        self.started.emit()
        try:
            try:
                import pyautogui  # type: ignore
            except Exception as e:
                self.progress.emit(f"pyautogui gerekli: {e}")
                self.finished.emit(False)
                return

            path = Path(self.coords_path)
            if not path.exists():
                self.progress.emit(f"Koordinat dosyası bulunamadı: {path}")
                self.finished.emit(False)
                return

            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                self.progress.emit(f"Koordinat dosyası okunamadı: {e}")
                self.finished.emit(False)
                return

            if not isinstance(data, list):
                self.progress.emit("Koordinat dosyası list formatında değil.")
                self.finished.emit(False)
                return

            count = 0
            for item in data:
                if self._abort:
                    self.progress.emit("İşlem iptal edildi.")
                    self.finished.emit(False)
                    return

                # Her adımda interval'ı config.json'dan oku → kullanıcı anlık ayarlayabilsin
                interval = _safe_read_interval()

                try:
                    x = int(item.get("x"))
                    y = int(item.get("y"))
                except Exception:
                    continue

                pyautogui.moveTo(x, y, duration=0)
                pyautogui.click()
                count += 1
                self.progress.emit(f"Tıklandı: ({x}, {y}) — {count}. adım (interval: {interval:.3f}s)")

                # JITTER YOK — sadece sabit bekleme. 0 ise hiç bekleme yapma.
                if interval > 0.0:
                    time.sleep(interval)

            self.progress.emit(f"Bitti. Toplam {count} tıklama.")
            self.finished.emit(True)

        except Exception as e:
            self.progress.emit(f"Hata: {e}")
            self.finished.emit(False)

    def abort(self):
        self._abort = True
