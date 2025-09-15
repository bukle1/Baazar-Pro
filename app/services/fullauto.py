from __future__ import annotations
import re
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, Callable
import json, os
try:
    import pytesseract  # type: ignore
except Exception:
    pytesseract = None  # type: ignore
try:
    from rapidfuzz import process, fuzz  # type: ignore
except Exception:
    process = None
    fuzz = None
import unicodedata

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

# Local services (package-relative)
try:
    from .buy_service import BuyService
except Exception:
    BuyService = None  # type: ignore

try:
    from .collect_service import CollectAndSellService
except Exception:
    CollectAndSellService = None  # type: ignore


try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05  # küçük global bekleme
except Exception:
    pass

class FullAutoService:
    """
    Tam otomatik akış (INSERT ile başlat/durdur):

      1) BuyService (bloklayıcı beklenir)
      2) CollectAndSellService (SELL fazı — kendi mantığın; green/yüzde yoksa self stop)
      3) Turuncu fazı: turuncu.png bulunup tıklanır, ardından (887,428) tıklanır;
         turuncu.png bulunmadığı ana kadar devam eder.
      4) 1. adıma dön.
    """

    def __init__(
        self,
        region_topleft: Tuple[int, int] = (795, 373),
        region_bottomright: Tuple[int, int] = (1121, 519),
        orange_template: Path = Path("app/data/template/turuncu.png"),
        template_thresh: float = 0.70,
        post_orange_click: Tuple[int, int] = (887, 428),
        # --- OCR / ROI parametreleri (kullanıcı talebi) ---
        name_offset_x: int = 77,
        name_offset_y: int = -32,
        name_roi_w: int = 300,
        name_roi_h: int = 25,
        ocr_confidence_cutoff: int = 70,
        # --- beklemeler ---
        sleep_short: float = 2.0,
        sleep_long: float = 3.0,
        # debug
        ocr_debug_dir: Optional[Path] = Path(__file__).resolve().parents[2] / "app" / "data" / "debug",
        hotkey: Optional[str] = "insert",
        log_callback: Optional[Callable[[str], None]] = None,
        collect_max_seconds: Optional[float] = None,
    ):
        self.region_topleft = region_topleft
        self.region_bottomright = region_bottomright
        self.orange_template = orange_template
        self.template_thresh = float(template_thresh)
        self.post_orange_click = post_orange_click
        self.collect_max_seconds = collect_max_seconds

        # --- OCR/ROI ---
        self.name_offset_x = int(name_offset_x)
        self.name_offset_y = int(name_offset_y)
        self.name_roi_w = int(name_roi_w)
        self.name_roi_h = int(name_roi_h)
        self.ocr_confidence_cutoff = int(ocr_confidence_cutoff)
        self.sleep_short = float(sleep_short)
        self.sleep_long = float(sleep_long)
        self.ocr_debug_dir = ocr_debug_dir
        self._ocr_counter = 0
        # selecteditems.json hazırlığı için state
        self._orange_prepared = False
        self._expected_cache = {}
        self._should_buy_next = True  # ilk turda buy ile başla


        self.log = log_callback or (lambda m: print(f"[fullauto] {m}"))
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hotkey = hotkey

        # Services
        self.buy_service = BuyService(log_callback=self.log, hotkey=None) if BuyService else None
        self.collect_service = CollectAndSellService(log_callback=self.log, hotkey=None) if CollectAndSellService else None

        # Hotkey register (optional)
        try:
            if self._hotkey and keyboard:
                keyboard.add_hotkey(self._hotkey, self.toggle)
                self.log(f"Global {self._hotkey.upper()} kısayolu aktif (FullAuto).")
        except Exception as e:
            self.log(f"Kısayol eklenemedi: {e}")

    
    # ---------- Public API ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if pyautogui is None or cv2 is None or np is None:
            self.log("Gerekli modüller yok (pyautogui/cv2/numpy). Başlatılamadı.")
            return
        if self.buy_service is None or self.collect_service is None:
            self.log("Gerekli servisler import edilemedi. Başlatılamadı.")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._main_loop, name="fullauto", daemon=True)
        self._thread.start()
        self.log("FullAuto başladı.")

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_evt.set()
            # Alt servisleri de durdur
            try:
                if self.collect_service:
                    self.collect_service.stop()
            except Exception:
                pass
            try:
                if self.buy_service:
                    self.buy_service.stop()
            except Exception:
                pass
            self.log("FullAuto durduruluyor...")

    def toggle(self):
        if self._thread and self._thread.is_alive():
            self.stop()
        else:
            self.start()

    # ---------- Internals ----------
    def _sleep(self, t: float = 2.0):
        time.sleep(max(0.0, float(t)))

    def _grab_region(self):
        x1, y1 = self.region_topleft
        x2, y2 = self.region_bottomright
        w, h = x2 - x1, y2 - y1
        shot = pyautogui.screenshot(region=(x1, y1, w, h))  # PIL Image (RGB)
        frame = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
        self._sleep(0.10)
        return frame

    def _match_center(self, frame, tpl_path: Path):
        if not tpl_path.exists():
            self.log(f"Şablon yok: {tpl_path}")
            return None
        tpl = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
        if tpl is None:
            self.log(f"Şablon okunamadı: {tpl_path}")
            return None
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        if max_val >= self.template_thresh:
            th, tw = tpl.shape[:2]
            top_left = max_loc
            cx = self.region_topleft[0] + top_left[0] + tw // 2
            cy = self.region_topleft[1] + top_left[1] + th // 2
            return (cx, cy, float(max_val))
        return None

    def _click_xy(self, x: int, y: int, label: str = ""):
        # Stabilite için direkt koordinata tıkla (move+click yerine)
        pyautogui.click(x, y)
        if label:
            self.log(f"Tık: {label} -> ({x},{y})")
        self._sleep(0.10)  # 0.10–0.15 arası güvenli

    def _run_buy_blocking(self):
        try:
            self.buy_service.start()
            while True:
                th = getattr(self.buy_service, "_thread", None)
                if th is None or not th.is_alive() or self._stop_evt.is_set():
                    break
                self._sleep(0.4)
        except Exception as e:
            self.log(f"BuyService hata: {e}")


    # ---------- SelectedItems yardımcıları ----------
    def _selected_path(self) -> Path:
        return Path("app/data/selecteditems.json")

    def _backup_selected(self) -> dict:
        """
        Cache yapısı:
        key -> {"amount": int, "orig": str}
        key türetme: lower-case ve boşluksuz varyantlar; fuzzy ve doğrudan eşleşmeler için sağlam.
        """
        p = self._selected_path()
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items = data.get("items") or []
            cache = {}
            for it in items:
                orig = (it.get("name") or "").strip()
                amt  = int(it.get("expected_amount") or 1)
                if not orig:
                    continue
                k1 = orig.lower().strip()
                k2 = k1.replace(" ", "")
                cache[k1] = {"amount": amt, "orig": orig}
                cache[k2] = {"amount": amt, "orig": orig}
            return cache
        except Exception as e:
            self.log(f"selecteditems.json okunamadı (backup): {e}")
            return {}



    def _reset_selected(self):
        p = self._selected_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = { "items": [], "saved_at": int(__import__("time").time()) }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log("selecteditems.json sıfırlandı (orange phase başlangıcı).")

    def _append_selected(self, name: str, expected_amount: int):
        p = self._selected_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}
        except Exception:
            data = {"items": []}
        items = data.get("items") or []
        # Aynı isim varsa tekrar eklemeyelim
        names_lc = { (i.get("name") or "").strip().lower() for i in items }
        if (name or "").strip().lower() in names_lc:
            self.log(f"selecteditems.json zaten içeriyor: {name}")
        else:
            items.append({"id": "", "name": name, "expected_amount": int(expected_amount)})
            data["items"] = items
            data["saved_at"] = int(__import__("time").time())
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"selecteditems.json eklendi: {name} → {expected_amount}")

    # ---------- OCR yardımcıları ----------
    def _ensure_debug_dir(self):
        try:
            if self.ocr_debug_dir:
                Path(self.ocr_debug_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"Debug dizini oluşturulamadı: {e}")
    def _ocr_item_name(self, center_x: int, center_y: int) -> str:
        # ROI (mouse merkezine göre)
        x = int(center_x + self.name_offset_x)
        y = int(center_y + self.name_offset_y)
        w = int(self.name_roi_w)
        h = int(self.name_roi_h)

        # pyautogui/cv2/np kontrolü
        if pyautogui is None or cv2 is None or np is None:
            self.log("OCR: pyautogui/cv2/numpy yok; debug da üretilemedi.")
            return ""

        # Ekran görüntüsü (ROI + opsiyonel full)
        roi_shot = pyautogui.screenshot(region=(x, y, w, h))
        try:
            full_shot = pyautogui.screenshot()
        except Exception:
            full_shot = None

        frame = cv2.cvtColor(np.array(roi_shot), cv2.COLOR_RGB2BGR)
        full_frame = cv2.cvtColor(np.array(full_shot), cv2.COLOR_RGB2BGR) if full_shot is not None else None

        # Basit iyileştirme
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LINEAR)
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Debug kayıt (Tesseract olsa da olmasa da KAYDET)
        self._ensure_debug_dir()
        self._ocr_counter += 1
        dbg_dir = Path(self.ocr_debug_dir).resolve() if self.ocr_debug_dir else Path("app/data/debug").resolve()
        try:
            dbg_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"Debug dizini oluşturulamadı: {e}")

        dbg_raw = (dbg_dir / f"ocr_{self._ocr_counter:04d}_roi_raw.png").resolve()
        dbg_th  = (dbg_dir / f"ocr_{self._ocr_counter:04d}_roi_th.png").resolve()
        cv2.imwrite(str(dbg_raw), frame)
        cv2.imwrite(str(dbg_th), th)

        dbg_full = None
        if full_frame is not None:
            try:
                dbg_full = (dbg_dir / f"ocr_{self._ocr_counter:04d}_screen_annot.png").resolve()
                cv2.rectangle(full_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.imwrite(str(dbg_full), full_frame)
            except Exception:
                pass

        # OCR
        text = ""
        if pytesseract is None:
            self.log(f"OCR: pytesseract bulunamadı. Debug kaydedildi → raw:{dbg_raw} th:{dbg_th} screen:{dbg_full}")
        else:
            # tek satır + whitelist
            config = "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 "
            try:
                text = pytesseract.image_to_string(th, config=config)
            except Exception as e:
                self.log(f"OCR çalıştırılamadı: {e}")

        raw_text = (text or "").strip()

        # Temizle (aksanları/işaretleri at; tire vb. dahil hepsini kaldırıyoruz)
        text = self._sanitize_name(raw_text)

        if text:
            self.log(f"OCR raw='{raw_text}' → clean='{text}' (ROI x={x},y={y},w={w},h={h}) → raw:{dbg_raw} th:{dbg_th} screen:{dbg_full}")
        else:
            self.log(f"OCR boş (raw='{raw_text}') (ROI x={x},y={y},w={w},h={h}) → raw:{dbg_raw} th:{dbg_th} screen:{dbg_full}")

        return text

    def _fuzzy_fix_name(self, raw: str) -> str:
        if not raw:
            return ""
        if not self._expected_cache:
            return raw

        # Aranacak denemeler: spaced + raw + nospace (hepsi lower)
        base = raw.strip()
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
        spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
        spaced = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", spaced)
        spaced = re.sub(r"\s+", " ", spaced).strip()

        attempts = [
            spaced.lower(),
            base.lower(),
            spaced.replace(" ", "").lower(),
            base.replace(" ", "").lower(),
        ]

        candidates = list(self._expected_cache.keys())

        try:
            if process and fuzz:
                for a in attempts:
                    match = process.extractOne(a, candidates, scorer=fuzz.WRatio)
                    if match and match[1] >= self.ocr_confidence_cutoff:
                        hit = match[0]
                        orig_name = self._expected_cache.get(hit, {}).get("orig") or base
                        self.log(f"Fuzzy eşleşme: '{raw}' -> '{orig_name}' (score={match[1]})")
                        return orig_name
        except Exception as e:
            self.log(f"Fuzzy hata: {e}")

        return raw


    def _run_collect_blocking(self):
        try:
            self.collect_service.start()
            t0 = time.time()
            while True:
                th = getattr(self.collect_service, "_thread", None)
                alive = bool(th and th.is_alive())
                if not alive or self._stop_evt.is_set():
                    break
                if self.collect_max_seconds is not None and (time.time() - t0) > float(self.collect_max_seconds):
                    self.log("Collect&Sell süre sınırı aşıldı, durduruluyor...")
                    self.collect_service.stop()
                    break
                self._sleep(0.4)
        except Exception as e:
            self.log(f"CollectAndSell hata: {e}")
        finally:
            try:
                self.collect_service.stop()
            except Exception:
                pass



        
    def _orange_phase(self) -> bool:
        """
        Orange phase:
        - İlk girişte selecteditems.json'u sıfırlar ve eski miktar+isimleri cache'e alır.
        - Turuncu itemler için OCR ile isim okur, cache'den varsa orijinal isim ve miktarı bulur.
        - Her item için selecteditems.json'a ekler.
        Dönüş:
        True  -> Bu turda en az bir item işlendi/eklendi (birileri önümüze kırmış).
        False -> Bu turda hiçbir item çıkmadı (kırılma yok).
        """
        changed = False

        # Orange'a ilk giriş: cache hazırla ve json'u temizle
        if not self._orange_prepared:
            self._expected_cache = self._backup_selected()
            self._reset_selected()
            self._orange_prepared = True

        self._sleep(self.sleep_short)

        while not self._stop_evt.is_set():
            frame = self._grab_region()
            hit = self._match_center(frame, self.orange_template)
            if not hit:
                self.log("turuncu.png bulunamadı -> Orange aşaması bitti.")
                pyautogui.moveTo(959, 501, duration=0)
                pyautogui.click()
                break

            x, y, score = hit

            # 1) Turuncu merkeze tıklama yok, sadece hover
            pyautogui.moveTo(x, y, duration=0)
            self.log(f"TURUNCU hover (score={score:.3f}) @ ({x},{y})")

            # 1.5) OCR: iki kez oku, ikisi aynıysa onu al (dalgalanmayı azalt)
            name_raw1 = self._ocr_item_name(x, y)
            self._sleep(0.2)
            name_raw2 = self._ocr_item_name(x, y)
            if name_raw2 and name_raw2 == name_raw1:
                name_raw = name_raw2
            else:
                name_raw = max([name_raw1, name_raw2], key=lambda s: len(s or ""), default=name_raw1)

            # Fuzzy fix (cache'deki orijinal ismi tercih eder)
            name_key = self._fuzzy_fix_name(name_raw).strip()
            if not name_key:
                name_key = (name_raw or "").strip()

            # Cache'ten miktar ve orijinal isim bul
            ckey = (name_key or "").strip().lower()
            ckey_ns = ckey.replace(" ", "")
            hit_cache = self._expected_cache.get(ckey) or self._expected_cache.get(ckey_ns)
            exp = int((hit_cache or {}).get("amount") or 1)

            # 2) Kısa bekleme (UI state gelsin)
            self._sleep(self.sleep_short)
            pyautogui.click()
            self._sleep(self.sleep_short)

            # 3) post-orange klik
            px, py = self.post_orange_click
            self._click_xy(px, py, "post-orange (887,428)")

            # 4) Minik bekleme — 3 sn
            self._sleep(self.sleep_long)

            # 5) selecteditems.json'a ekleme
            if name_key:
                # Cache'ten orijinal isim varsa onu, yoksa OCR'dan geleni yaz
                to_write = (hit_cache or {}).get("orig") or (name_raw if name_raw else name_key)
                self._append_selected(to_write, exp)
                changed = True
            else:
                self.log("Uyarı: OCR adı boş geldi; bu tur item eklenmedi.")

        return changed





            
            

    def _main_loop(self):
        try:
            # İlk tur davranışı
            next_is_buy = bool(getattr(self, "_should_buy_next", True))

            while not self._stop_evt.is_set():
                if next_is_buy:
                    # Buy
                    self._run_buy_blocking()
                    if self._stop_evt.is_set():
                        break
                else:
                    # Collect
                    self._run_collect_blocking()
                    if self._stop_evt.is_set():
                        break

                # Orange -> kırılma var mı bak
                had_changes = self._orange_phase()
                if self._stop_evt.is_set():
                    break

                # Eğer bu tur kırılma olduysa sırada BUY; olmadıysa COLLECT
                next_is_buy = bool(had_changes)
                self._should_buy_next = next_is_buy

        finally:
            self.log("FullAuto durdu.")
            
            
            
            
    def _sanitize_name(self, s: str) -> str:
        # aksanları at
        s_norm = unicodedata.normalize("NFKD", s)
        s_ascii = s_norm.encode("ASCII", "ignore").decode("ASCII")
        # sadece harf-rakam-boşluk kalsın (tire dahil tüm noktalama gider)
        s_ascii = re.sub(r"[^A-Za-z0-9 ]+", " ", s_ascii)
        s_ascii = re.sub(r"\s+", " ", s_ascii).strip()
        return s_ascii