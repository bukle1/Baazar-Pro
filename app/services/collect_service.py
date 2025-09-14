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
try:
    from app.fastsell import FastSellWorker
except Exception:
    # Yerel import hata verirse, minimal bir stub ile devam edelim ki import patlamasın.
    class FastSellWorker:  # type: ignore
        def __init__(self, coords_path: str = "app/data/coordinates.json"):
            self.coords_path = coords_path
        def run(self):
            raise RuntimeError("FastSellWorker bulunamadı. app.fastsell modülünü sağladığınızdan emin olun.")


class CollectAndSellService:
    """Collect & Sell döngüsünü yöneten servis.
    
    - start()/stop()/toggle() metodları ile kontrol edilir.
    - UI'dan bağımsızdır; log_callback ile mesaj iletebilir.
    - Koordinatlar ve template yolu parametrelenmiştir.
    - İstenen bölgenin ekran görüntüsünü her seferinde debug dosyasına (üzerine yazarak) kaydeder.
    """

    def __init__(
        self,
        click_pos: Tuple[int, int] = (1000, 534),
        region_topleft: Tuple[int, int] = (795, 373),
        region_bottomright: Tuple[int, int] = (1121, 519),
        template_path: Path = Path("app/data/template/green.png"),
        template_thresh: float = 0.85,
        coords_path: str = "app/data/coordinates.json",
        log_callback: Optional[Callable[[str], None]] = None,
        hotkey: Optional[str] = "f1",
        # --- Çoklu template desteği ---
        
        green_template_paths: Optional[list[Path]] = None,
        yuzde_template_paths: Optional[list[Path]] = None,
        # --- Debug görüntü kaydı için eklenen parametreler ---
        debug_save: bool = True,
        debug_path: Path = Path("debug.png"),  # .png veya .jpg; timestamp YOK, her seferinde ÜSTÜNE YAZAR
    ):

        self.click_pos = click_pos
        self.region_topleft = region_topleft
        self.region_bottomright = region_bottomright
        self.template_path = template_path
        self.template_thresh = float(template_thresh)
        self.coords_path = coords_path
        self.log = log_callback or (lambda msg: print(f"[collect-svc] {msg}"))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hotkey = hotkey

        self.green_templates: list[Path] = green_template_paths or [
            Path("app/data/template/green1.png"),
            Path("app/data/template/green2.png"),
            Path("app/data/template/green3.png"),
            Path("app/data/template/green4.png"),
        ]
    
        # Yüzde şablonları
        self.template_paths2: list[Path] = yuzde_template_paths or [
            Path("app/data/template/yuzde1.png"),
            Path("app/data/template/yuzde2.png"),
            Path("app/data/template/yuzde3.png"),
        ]

        # Debug kayıt opsiyonları
        self.debug_save = bool(debug_save)
        self.debug_path = debug_path

        # Hotkey (opsiyonel)
        try:
            if hotkey and keyboard:
                keyboard.add_hotkey(hotkey, self.toggle)
                self.log(f"Global {hotkey.upper()} kısayolu aktif (Collect&Sell).")
        except Exception as e:
            self.log(f"Global kısayol eklenemedi: {e}")

    # ---------- Public API ----------
    def start(self):
        """Servisi başlatır (zaten çalışıyorsa tekrar başlatmaz)."""
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
        """Servisi durdurur."""
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self.log("Döngü durduruluyor...")

    def toggle(self):
        """Çalışıyorsa durdurur, duruyorsa başlatır."""
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

        # # --- DEBUG: her yakalamada aynı dosyaya yaz (timestamp YOK) ---
        # if self.debug_save and cv2 is not None:
        #     try:
        #         # Klasör yoksa oluştur
        #         if self.debug_path.parent and not self.debug_path.parent.exists():
        #             self.debug_path.parent.mkdir(parents=True, exist_ok=True)
        #         # Üzerine yaz: PNG/JPG fark etmez, uzantıya göre kaydedilir
        #         ok = cv2.imwrite(str(self.debug_path), frame)
        #         if not ok:
        #             self.log(f"Debug görüntüsü kaydedilemedi: {self.debug_path}")
        #         else:
        #             self.log(f"Debug görüntüsü kaydedildi: {self.debug_path}")
        #     except Exception as e:
        #         self.log(f"Debug görüntüsü kaydetme hatası: {e}")

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
            self.log(f"Bulundu: {tpl_path.name} (score={max_val:.3f}) → tık: ({cx},{cy})")
            pyautogui.moveTo(1115, 532, duration=0)
            return True
        else:
            self.log(f"Eşleşme yok ({tpl_path.name}), max={max_val:.3f}")
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

        # --- GREEN: birini bulursan yeter, yoksa pas geç ---
        green_found = False
        for gtpl in self.green_templates:
            frame = self._grab_region()
            if self._match_and_click_center(frame, gtpl):
                green_found = True
                break
            # küçük nefes payı
            time.sleep(0.15)

        time.sleep(0.45)

        # --- YÜZDE: her bir tpl'i EKRANDA KALMAYANA KADAR tıkla ---
        for tpl in self.template_paths2:
            # güvenlik sayaçlı sonsuz olmayan döngü (opsiyonel)
            safety = 0
            while True:
                frame = self._grab_region()
                if not self._match_and_click_center(frame, tpl):
                    break
                safety += 1
                if safety > 5:
                    self.log(f"Uyarı: {tpl.name} için güvenlik sınırı aşıldı.")
                    break

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
