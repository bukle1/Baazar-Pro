
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional, Callable, Tuple
import json
# Third-party (optional at import-time)
try:
    import pyautogui  # type: ignore
    import keyboard   # type: ignore
except Exception:
    pyautogui = None  # type: ignore
    keyboard = None   # type: ignore

CONFIG_PATH = Path("app/data/config.json")
SELECTED_PATH = Path("app/data/selecteditems.json")


COORDS = {
    "a":  (952, 427), "a1": (940, 472), "a2": (950, 512),
    "b":  (886, 392), "b1": (926, 391), "b2": (957, 391), "b3": (997, 391),"b4": (1027, 391),
}

# Bu listeyi sen dolduracaksın. Anahtar: item adı (küçük harf), Değer: a/b varyantı
# Örnekler:
SPECIAL = {
    "Emerald": {"a": "a2"},            # Emerald için a2 tıklansın
    "Nether Wart": {"a": "a1", "b": "b2"}, 
    "Snow Block": {"b": "b1"}, 
    "Plasma": {"a": "a1"}, 
    "Feather": {"a": "a1", "b": "b2"}, 
    "Cocoa Beans": {"a": "a1"}, 
    "Prismarine Shard": {"a": "a1", "b": "b1"}, 
    "Jungle Log": {"a": "a1", "b": "b1"}, 
    "Blaze Ashes": {"a": "a2"}, 
    "Iron Ingot": {"b": "b1"}, 
    "Sand": {"a": "a3", "b": "b4"}, 
    "Lily Pad": {"b": "b1"}, 
}

SPECIAL = { (k or "").strip().lower(): v for k, v in SPECIAL.items() }

def _safe_read_interval() -> float:
    """
    app/data/config.json içinden fastsell.interval'ı güvenle okur.
    Bulunamazsa veya hatalıysa 0.3 döner.
    {
      "fastsell": { "interval": 0.3 }
    }
    """
    try:
        if not CONFIG_PATH.exists():
            return 0.3
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        fs = (cfg.get("fastsell") or {})
        val = float(fs.get("interval", 0.3) or 0.3)
        if val < 0:
            val = 0.0
        return val
    except Exception:
        return 0.3


class BuyService:
    


    """
    selecteditems.json içindeki her item için sırasıyla Buy Order otomasyonu.

    Akış (her item için):
      1) (817, 540) tıkla
      2) ismi (name) yaz
      3) (952, 427) tıkla
      4) (886, 392) tıkla
      5) (1022, 430) tıkla
      6) (1072, 427) tıkla
      7) expected_amount yaz ve Enter
      8) (927, 431) tıkla
      9) (965, 431) tıkla
     10) 'x' gönder (kapat) ve sonraki item'a geç
    """

    def __init__(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
        hotkey: Optional[str] = "f2",
        # Koordinatlar sabit verildi; gerekirse ctor ile özelleştirilebilir
        c_search: Tuple[int, int] = (817, 540),
        c_a: Tuple[int, int] = (952, 427),
        c_b: Tuple[int, int] = (886, 392),
        c_c: Tuple[int, int] = (1022, 430),
        c_d: Tuple[int, int] = (1072, 427),
        c_e: Tuple[int, int] = (927, 431),
        c_f: Tuple[int, int] = (965, 431),
    ):
        self.log = log_callback or (lambda m: print(f"[buy-svc] {m}"))
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hotkey = hotkey

        # Coords
        self.c_search = c_search
        self.c_a = c_a
        self.c_b = c_b
        self.c_c = c_c
        self.c_d = c_d
        self.c_e = c_e
        self.c_f = c_f

        try:
            if hotkey and keyboard:
                keyboard.add_hotkey(hotkey, self.toggle)
                self.log(f"Global {hotkey.upper()} kısayolu aktif (BuyService).")
        except Exception as e:
            self.log(f"Kısayol eklenemedi: {e}")

    # ---------- Public API ----------
    def _pick(self, name_lc: str, group: str) -> tuple[int, int]:
        # group "a" veya "b" gelecek. default "a"/"b", SPECIAL varsa a1/a2 veya b1/b2/b3
        variant = SPECIAL.get(name_lc, {}).get(group, group)
        return COORDS[variant]

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if pyautogui is None:
            self.log("pyautogui gerekli; import edilemedi.")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="buy-service", daemon=True)
        self._thread.start()
        self.log("BuyService döngü başladı.")

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_evt.set()
            self.log("BuyService durduruluyor...")

    def toggle(self):
        if self._thread and self._thread.is_alive():
            self.stop()
        else:
            self.start()

    # ---------- Internals ----------
    def _sleep(self):
        # Her adım arası bekleme: config'ten oku
        t = _safe_read_interval()
        if t > 0:
            time.sleep(t)
 
    def _click(self, xy: Tuple[int, int], label: str = ""):
        x, y = xy
        pyautogui.moveTo(x, y, duration=0)
        pyautogui.click()
        if label:
            self.log(f"Tık: {label} → ({x},{y})")
        self._sleep()

    def _type(self, text: str, press_enter: bool = False, clear_first: bool = True):

        pyautogui.typewrite(text, interval=0.01)
        self.log(f"Yazıldı: '{text}'")
        self._sleep()
        if press_enter and keyboard:
            keyboard.send("enter")
            self._sleep()


    def _press_x(self):
        if keyboard:
            keyboard.send("x")
        self._sleep()

    def _load_items(self) -> list[dict]:
        if not SELECTED_PATH.exists():
            self.log(f"Seçim dosyası yok: {SELECTED_PATH}")
            return []
        try:
            data = json.loads(SELECTED_PATH.read_text(encoding="utf-8"))
            items = data.get("items") or []
            # Sıra korunur (JSON listesi zaten sıralıdır)
            return items
        except Exception as e:
            self.log(f"Seçim dosyası okunamadı: {e}")
            return []

    def _run_one_item(self, name: str, expected_amount: int):
        # 0) Önce x e basarak bazarı aç
        self._press_x()
        # 1) Arama alanına tıkla ve isim yaz
        self._click(self.c_search, "search")
        self._type(name)

        # 3-6) Dört farklı tıklama sırası
        name_lc = (name or "").strip().lower()
        self._click(self._pick(name_lc, "a"), "a*")
        self._click(self._pick(name_lc, "b"), "b*")
        self._click(self.c_c, "c")
        self._click(self.c_d, "d")

        # 7) expected amount yaz + Enter
        self._type(str(int(expected_amount)), press_enter=True)

        # 8-9) onay tıklamaları
        time.sleep(1)
        self._click(self.c_e, "e")
        time.sleep(1)
        self._click(self.c_f, "f")

        # 10) X
        self._press_x()


    def _run(self):
        items = self._load_items()
        if not items:
            self.log("İşlenecek item yok.")
            return
        self.log(f"{len(items)} adet item işlenecek.")

        for idx, it in enumerate(items, 1):
            if self._stop_evt.is_set():
                break

            name = str(it.get("name") or "").strip()
            if not name:
                self.log(f"{idx}. kayıt atlandı (isim yok).")
                continue

            exp = it.get("expected_amount")
            try:
                exp = int(exp)
            except Exception:
                exp = 1

            self.log(f"[{idx}/{len(items)}] {name} → {exp}")
            try:
                self._run_one_item(name, exp)
            except Exception as e:
                self.log(f"Hata (item='{name}'): {e}")
                self._sleep()

            # 🔑 Her 5 işlemde 1 dakika bekle
            if idx % 5 == 0:
                self.log("5 adet buy order tamamlandı. 1 dakika bekleniyor...")
                time.sleep(65)   # 65 saniye bekle

        self.log("BuyService tamamlandı.")