
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Tuple, Optional, Callable

# Third-party (optional at import-time)
try:
    import pyautogui  # type: ignore
    import keyboard   # type: ignore
    import cv2        # type: ignore
    import numpy as np
except Exception:
    pyautogui = None  # type: ignore
    keyboard = None   # type: ignore
    cv2 = None        # type: ignore
    np = None         # type: ignore

# Local
from app.fastsell import FastSellWorker

class CollectAndSellService:
    """Collect & Sell döngüsünü yöneten bağımsız servis.
    
    - start()/stop()/toggle() metodları ile kontrol edilir.
    - UI'dan bağımsızdır; log_callback ile mesaj iletebilir.
    - Koordinatlar ve template yolu parametrelenmiştir.
    """

    def __init__(
        self,
        click_pos: Tuple[int, int] = (1000, 534),
        region_topleft: Tuple[int, int] = (795, 373),
        region_bottomright: Tuple[int, int] = (1121, 519),
        template_path: Path = Path("app/data/template/green.png"),
        template_path2: Path = Path("app/data/template/yuzde.png"),
        template_thresh: float = 0.85,
        coords_path: str = "app/data/coordinates.json",
        log_callback: Optional[Callable[[str], None]] = None,
        hotkey: Optional[str] = "f1",
    ):

        self.click_pos = click_pos
        self.region_topleft = region_topleft
        self.region_bottomright = region_bottomright
        self.template_path = template_path
        self.template_paths2 = [
            Path("app/data/template/yuzde1.png"),
            Path("app/data/template/yuzde2.png"),
            Path("app/data/template/yuzde3.png")
        ]
        # ikinci arama için yuzde template
        self.template_path2 = Path('app/data/template/yuzde.png')

        self.template_thresh = template_thresh
        self.coords_path = coords_path
        self.log = log_callback or (lambda msg: print(f"[collect-svc] {msg}"))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hotkey = hotkey

        # Hotkey (optional)
        try:
            if hotkey and keyboard:
                keyboard.add_hotkey(hotkey, self.toggle)
                self.log(f"Global {hotkey.upper()} kısayolu aktif (collect&sell)."
                )
        except Exception as e:
            self.log(f"Global kısayol eklenemedi: {e}")

    # ---------- Public API ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if pyautogui is None or cv2 is None or np is None:
            self.log("Gerekli modüller yok (pyautogui/cv2/numpy). Servis başlatılamadı.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop_body, name="collect-sell", daemon=True)
        self._thread.start()
        self.log("Döngü başladı.")

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self.log("Döngü durduruluyor...")

    def toggle(self):
        if self._thread and self._thread.is_alive():
            self.stop()
        else:
            self.start()

    # ---------- Internals ----------
    def _press_x_and_click(self):
        if keyboard:
            keyboard.send("x")
        time.sleep(0.31)
        x, y = self.click_pos
        pyautogui.moveTo(x, y, duration=0)
        pyautogui.click()
        time.sleep(0.31)

    def _grab_region(self):
        x1, y1 = self.region_topleft
        x2, y2 = self.region_bottomright
        w, h = x2 - x1, y2 - y1
        shot = pyautogui.screenshot(region=(x1, y1, w, h))  # RGB PIL Image
        frame = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
        time.sleep(0.45)
        return frame

    def _match_and_click_center(self, frame, tpl_path: Path) -> bool:
        if not tpl_path.exists():
            self.log(f"Şablon yok: {tpl_path}")
            return False
        tpl = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
        if tpl is None:
            self.log(f"Şablon okunamadı: {tpl_path}")
            return False
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        found = max_val >= self.template_thresh
        if found:
            th, tw = tpl.shape[:2]
            top_left = max_loc
            cx = self.region_topleft[0] + top_left[0] + tw // 2
            cy = self.region_topleft[1] + top_left[1] + th // 2
            pyautogui.moveTo(cx, cy, duration=0)
            pyautogui.click()
            self.log(f"Yeşil bulundu (score={max_val:.3f}) → tık: ({cx},{cy})")
            return True
        else:
            self.log(f"Yeşil bulunamadı (max={max_val:.3f})")

            return False

    def _press_esc_and_click(self):
        if keyboard:
            keyboard.send("esc")
        time.sleep(0.45)
        pyautogui.click()
        time.sleep(0.45)

    def _run_fastsell_blocking(self):
        worker = FastSellWorker(coords_path=self.coords_path)
        try:
            worker.run()  # blocking
        except Exception as e:
            self.log(f"FastSell hata: {e}")

    def _one_cycle(self):
        self._press_x_and_click()
        self._match_and_click_center(self._grab_region(), self.template_path)
        time.sleep(0.45)
        found=False
        for tpl in self.template_paths2:
            if self._match_and_click_center(self._grab_region(), tpl):
                found=True
                break
        if not found:
            self.log("Hiçbir yüzde şablonu bulunamadı")
        time.sleep(0.45)
        self._press_esc_and_click()
        self._run_fastsell_blocking()

    def _loop_body(self):
        try:
            while not self._stop_event.is_set():
                self._one_cycle()
                time.sleep(0.25)
        finally:
            self.log("Döngü durdu.")

        # TODO: Add logic to also match self.template_path2 as needed.
