import sys, json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QSize, Slot, QFile, QTextStream
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QTabWidget,
    QScrollArea, QGridLayout, QFrame, QMessageBox, QGraphicsDropShadowEffect
)

from app.workers import ScanWorker
from app.fastsell import FastSellWorker
from app.services.collect_service import CollectAndSellService
from app.services.buy_service import BuyService


# ------ formatting helpers ------
def fmt_int(n):
    try:
        n = int(round(float(n)))
    except Exception:
        return str(n)
    return f"{n:,}".replace(",", ".")  # Turkish dot group

def fmt_no_decimal(n):
    return fmt_int(n)

# ------ card widget ------
class Card(QFrame):
    def __init__(self, payload: dict, lines: list[tuple[str,str]], on_click=None, font_scale=1.0, is_selected=False):
        super().__init__()
        self.payload = payload
        self.on_click = on_click
        self.setObjectName("card")
        self.setProperty("selected", "true" if is_selected else "false")
        self.setAttribute(Qt.WA_Hover, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12,12,12,12)
        lay.setSpacing(8)
        title = QLabel(payload.get("name","?"))
        tf = QFont()
        tf.setPointSize(11)
        tf.setBold(True)
        title.setFont(tf)
        lay.addWidget(title)
        for k,v in lines:
            row = QHBoxLayout()
            lk = QLabel(k)
            lk.setObjectName("key")
            lf = QFont()
            lf.setPointSize(9)
            lk.setFont(lf)
            try:
                lk.setAttribute(Qt.WA_TranslucentBackground, True)
                lk.setStyleSheet("background: transparent;")
                lk.setContentsMargins(0,0,0,0)
            except Exception:
                pass
            lv = QLabel(v)
            vf = QFont()
            vf.setPointSize(10)
            lv.setFont(vf)
            try:
                lv.setAttribute(Qt.WA_TranslucentBackground, True)
                lv.setStyleSheet("background: transparent;")
                lv.setContentsMargins(0,0,0,0)
            except Exception:
                pass
            row.addWidget(lk)
            row.addStretch(1)
            row.addWidget(lv)
            lay.addLayout(row)


    def mousePressEvent(self, e):
        if e.button()==Qt.LeftButton and self.on_click:
            self.on_click(self.payload)

# ------ tabs base ------
class CardsTab(QWidget):
    def __init__(self, name, on_card_click):
        super().__init__()
        self.name=name
        self.on_card_click=on_card_click
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setSpacing(10)
        self.grid.setContentsMargins(10,10,10,10)
        self.scroll.setWidget(self.inner)
        v = QVBoxLayout(self)
        v.addWidget(self.scroll)

    def populate(self, cards, cols=4):
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        r=c=0
        for card in cards:
            self.grid.addWidget(card, r, c)
            c+=1
            if c>=cols:
                c=0
                r+=1

# ------ MISC tab (FastSell settings) ------
class MiscTab(QWidget):
    def __init__(self, load_config, save_config, service: CollectAndSellService):
        super().__init__()
        self.load_config = load_config
        self.save_config = save_config
        self.service = service

        v = QVBoxLayout(self)
        title = QLabel("FastSell & Collect Ayarları")
        tf = QFont()
        tf.setPointSize(12)
        tf.setBold(True)
        title.setFont(tf)
        v.addWidget(title)

        row1 = QHBoxLayout()
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.0, 10.0)
        self.spin_interval.setDecimals(3)
        self.spin_interval.setSingleStep(0.05)
        self.spin_interval.setSuffix(" sn bekleme")
        row1.addWidget(QLabel("Bekleme (sabit interval):"))
        row1.addWidget(self.spin_interval, 1)
        v.addLayout(row1)

        # Collect toggle controls
        btn_row = QHBoxLayout()
        self.btn_collect_toggle = QPushButton("Collect & Sell Başlat/Durdur (F1)")
        btn_row.addWidget(self.btn_collect_toggle)
        v.addLayout(btn_row)

        self.btn_save = QPushButton("Kaydet")
        v.addWidget(self.btn_save)

        self.btn_save.clicked.connect(self._on_save)
        self.btn_collect_toggle.clicked.connect(self.service.toggle)
        QTimer.singleShot(0, self._load_now)

    def _load_now(self):
        cfg = self.load_config()
        fs = cfg.get("fastsell", {})
        self.spin_interval.setValue(float(fs.get("interval", 0.3)))

    def _on_save(self):
        val = float(self.spin_interval.value())
        cfg = self.load_config()
        fs = cfg.setdefault("fastsell", {})
        fs["interval"] = val
        ok = self.save_config(cfg)
        QMessageBox.information(self, "Kaydedildi", "Ayarlar kaydedildi (app/data/config.json).") if ok else QMessageBox.warning(self, "Hata", "Ayarlar kaydedilemedi.")

# ------ main window ------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bazaar Pro — Masaüstü (Qt)")
        self.resize(1280, 800)

        # --- controls & log BEFORE service so that early logs are safe ---
        self.btn_scan = QPushButton("Tara / Güncelle")
        self.btn_show_selected = QPushButton("Seçimleri Göster")
        self.spin_min_pct = QSpinBox()
        self.spin_min_pct.setRange(0, 1000)
        self.spin_min_pct.setPrefix("Min % ")
        self.spin_min_vol = QSpinBox()
        self.spin_min_vol.setRange(0, 1000000)
        self.spin_min_vol.setPrefix("Min Insta Hacim ")
        self.spin_min_vol.setValue(500)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Ara: İsim")

        # --- Sort controls (multi-key) ---
        self.sort_bar = QHBoxLayout()
        self.lbl_sort = QLabel("Sırala (tıklama sırası öncelik):")
        self.btn_sort_power = QPushButton("Power")
        self.btn_sort_unit  = QPushButton("Birim Kâr")
        self.btn_sort_cph   = QPushButton("Coins/saat")
        self.btn_sort_isell = QPushButton("InstaSell (saatlik)")
        self.btn_sort_ibuy  = QPushButton("InstaBuy (saatlik)")
        self.btn_sort_clear = QPushButton("Sıfırla")
        for b in [self.btn_sort_power, self.btn_sort_unit, self.btn_sort_cph, self.btn_sort_isell, self.btn_sort_ibuy, self.btn_sort_clear]:
            b.setCheckable(False)
        self.sort_bar.addWidget(self.lbl_sort)
        self.sort_bar.addSpacing(6)
        for b in [self.btn_sort_power, self.btn_sort_unit, self.btn_sort_cph, self.btn_sort_isell, self.btn_sort_ibuy, self.btn_sort_clear]:
            self.sort_bar.addWidget(b)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)

        # --- now create tabs & service ---
        self.tabs = QTabWidget()
        # temporary placeholder service to pass into MiscTab after creation
        self.collect_service = None

        self.tab_baz = CardsTab("Bazaar", lambda p:self.card_clicked(p,"baz"))
        self.tab_npc = CardsTab("NPC",    lambda p:self.card_clicked(p,"npc"))
        self.tab_rev = CardsTab("Reverse",lambda p:self.card_clicked(p,"rev"))

        # Collect service (decoupled) - after log widget exists
        self.collect_service = CollectAndSellService(
            template_path=Path("app/data/template/green.png"),
            coords_path="app/data/coordinates.json",
            log_callback=self._log_msg,
            hotkey="f1",
        )
        
        self.buy_service = BuyService(
            log_callback=self._log_msg,
            hotkey="f2",
        )

        self.tab_misc = MiscTab(self._load_config, self._save_config, self.collect_service)
        self.tabs.addTab(self.tab_baz, "Bazaar Flips")
        self.tabs.addTab(self.tab_npc, "NPC Flips")
        self.tabs.addTab(self.tab_rev, "Reverse NPC")
        self.tabs.addTab(self.tab_misc, "MISC")

        top = QHBoxLayout()
        top.addWidget(self.btn_scan)
        top.addSpacing(8)
        top.addWidget(self.btn_show_selected)
        top.addSpacing(8)
        top.addWidget(self.spin_min_pct)
        top.addSpacing(8)
        top.addWidget(self.spin_min_vol)
        top.addSpacing(8)
        top.addWidget(self.txt_search, 1)

        root = QWidget()
        v = QVBoxLayout(root)
        v.addLayout(top)
        v.addLayout(self.sort_bar)
        v.addWidget(self.tabs,1)
        v.addWidget(self.log)
        self.setCentralWidget(root)

        # data
        self.raw_rows = []
        self.sort_orders = {"baz": [], "npc": [], "rev": []}

        # --- expected amount heuristics ---
        # Toplam saatlik likiditenin (min(hourly_buy, hourly_sell)) %2'si kadar öner.
        # UI'dan ileride ayarlanabilir hale getirmek için şimdilik sabit tutuyoruz.
        self.expected_pct_of_hourly = 0.20
        self.expected_min_units = 1

        # --- selections storage ---
        # store as dict: id -> {"name": str, "expected_amount": int}
        self._selected = {}
        self._selected_path = Path("app/data/selecteditems.json")
        self._selected_load()

        # wiring
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_show_selected.clicked.connect(self._show_selected_dialog)
        self._ui_timer = QTimer(self)
        self._ui_timer.setSingleShot(True)
        self._ui_timer.timeout.connect(self._rebuild_all_now)
        self.spin_min_pct.valueChanged.connect(lambda *_: self._schedule_rebuild())
        self.spin_min_vol.valueChanged.connect(lambda *_: self._schedule_rebuild())
        self.txt_search.textChanged.connect(lambda *_: self._schedule_rebuild())
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # sort btns
        self.btn_sort_power.clicked.connect(lambda *_: self._push_sort_key("power"))
        self.btn_sort_unit.clicked.connect(lambda *_: self._push_sort_key("unit"))
        self.btn_sort_cph.clicked.connect(lambda *_: self._push_sort_key("coins_h"))
        self.btn_sort_isell.clicked.connect(lambda *_: self._push_sort_key("hourly_sell"))
        self.btn_sort_ibuy.clicked.connect(lambda *_: self._push_sort_key("hourly_buy"))
        self.btn_sort_clear.clicked.connect(self._clear_sort_keys)

        # initial scan
        QTimer.singleShot(300, self.start_scan)

        # Load dark theme
        try:
            f = QFile("app/ui/styles/dark.qss")
            if f.open(QFile.ReadOnly | QFile.Text):
                ts = QTextStream(f)
                self.setStyleSheet(ts.readAll())
                # Ek seçim stili
                try:
                    extra_css = """
QFrame#card[selected=\"true\"] { border: 1px solid #17d88b; background: rgba(23,216,139,0.035); }
QFrame#card[selected=\"true\"]:hover { border: 1px solid #22f0a0; background: rgba(34,240,160,0.06); }
"""
                    self.setStyleSheet(self.styleSheet() + extra_css + "\nQFrame#card QLabel { background: transparent; }")
                except Exception:
                    pass
        except Exception as e:
            self._log_msg(f"Tema yüklenemedi: {e}")
            # yine de seçim stilini ekle
            try:
                extra_css = """
QFrame#card[selected=\"true\"] { border: 1px solid #17d88b; background: rgba(23,216,139,0.035); }
QFrame#card[selected=\"true\"]:hover { border: 1px solid #22f0a0; background: rgba(34,240,160,0.06); }
"""
                self.setStyleSheet(self.styleSheet() + extra_css + "\nQFrame#card QLabel { background: transparent; }")
            except Exception:
                pass

    # ----- Config helpers -----
    def _cfg_path(self):
        return Path("app/data/config.json")

    def _load_config(self):
        p = self._cfg_path()
        if not p.exists():
            return {"fastsell": {"interval": 0.3}}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"fastsell": {"interval": 0.3}}

    def _save_config(self, cfg):
        try:
            p = self._cfg_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            self._log_msg(f"Ayar kaydedilemedi: {e}")
            return False

    # ----- Worker
    def start_scan(self):
        if getattr(self, "_thread", None):
            return
        self._thread = QThread(self)
        self._worker = ScanWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.started.connect(lambda: self._log_msg("Tarama başladı..."))
        self._worker.progress.connect(self._log_msg)
        self._worker.finished.connect(self.on_scan_finished)
        self._worker.finished.connect(lambda *_: self._thread.quit())
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self):
        self._thread=None
        self._worker=None

    def on_scan_finished(self, rows, ok):
        if ok:
            self.raw_rows = rows
            self._log_msg(f"Güncellendi: {len(rows)} ürün")
            self._schedule_rebuild()
        else:
            self._log_msg("Tarama başarısız.")

    # ----- expected amount hesaplayıcı -----
    def _calc_expected_amount(self, r: dict) -> int:
        try:
            hb = int(r.get("hourly_buy", int(r.get("buy_volume", 0)) // 24))
            hs = int(r.get("hourly_sell", int(r.get("sell_volume", 0)) // 24))
            liq = max(0, min(hb, hs))
            base = int(liq * self.expected_pct_of_hourly)
            # çok düşük likiditelerde en az 1 adet öner
            amt = max(self.expected_min_units, base)
            # spread çok dar ise (<= %0.3) biraz daha konservatif ol
            sp = float(r.get("spread_percent", 0.0))
            if sp <= 0.3:
                amt = max(self.expected_min_units, int(amt * 0.5))
            return int(amt)
        except Exception:
            return self.expected_min_units

    # ----- Sorting helpers -----
    def _mode_key(self):
        idx = self.tabs.currentIndex()
        return ["baz","npc","rev","misc"][idx]

    def _push_sort_key(self, key):
        mode = self._mode_key()
        if mode not in self.sort_orders:
            return
        order = self.sort_orders[mode]
        if key in order:
            order.remove(key)
        order.insert(0, key)
        self.sort_orders[mode] = order[:5]
        self._log_msg(f"Sıralama: {mode} -> {', '.join(self.sort_orders[mode])}")
        self._schedule_rebuild()

    def _clear_sort_keys(self):
        mode = self._mode_key()
        if mode in self.sort_orders:
            self.sort_orders[mode] = []
            self._log_msg(f"Sıralama sıfırlandı: {mode}")
            self._schedule_rebuild()

    def _on_tab_changed(self, idx):
        if idx in (0,1,2):
            self._schedule_rebuild()

    def _schedule_rebuild(self):
        self._ui_timer.start(80)

    def _rebuild_all_now(self):
        if getattr(self, '_is_rebuilding', False):
            return
        self._is_rebuilding = True
        self.setUpdatesEnabled(False)

        try:
            min_pct = float(self.spin_min_pct.value() or 0)
            min_vol = int(self.spin_min_vol.value() or 0)
            q = (self.txt_search.text() or "").strip().lower()

            def ok_common(r):
                if float(r.get("buy_price", 0)) <= 0 or float(r.get("sell_price", 0)) <= 0:
                    return False
                if int(r.get("sell_volume", 0)) < min_vol:
                    return False
                if int(r.get("buy_volume", 0)) < min_vol:
                    return False
                if q and q not in r.get("name", "").lower():
                    return False
                return True

            def sort_by(order_keys, fallback_key, items):
                keys = order_keys[:] if order_keys else [fallback_key]
                def keyfunc(x):
                    payload = x["payload"]
                    return tuple([-float(payload.get(k, 0)) for k in keys])
                items.sort(key=keyfunc)
                return items

            mode = self._mode_key()

            # ----- Bazaar cards
            if mode == "baz":
                baz = []
                for r in self.raw_rows:
                    if not ok_common(r):
                        continue
                    buy_p = float(r["buy_price"])
                    sell_p = float(r["sell_price"])
                    buy_vol = int(r["buy_volume"])
                    sell_vol = int(r["sell_volume"])
                    hourly_sell = int(r.get("hourly_sell", sell_vol // 24))
                    hourly_buy  = int(r.get("hourly_buy",  buy_vol  // 24))
                    margin = sell_p - buy_p
                    pct = (margin / buy_p) * 100 if buy_p else 0.0
                    if pct < min_pct:
                        continue
                    coins_h = margin * min(hourly_sell, hourly_buy)
                    spread_pct = float(r.get("spread_percent",0))
                    power = coins_h * max(spread_pct, 1)
                    baz.append({
                        "payload": {**r, "mode":"baz", "power": power, "unit": margin, "coins_h": coins_h,
                                    "hourly_sell": hourly_sell, "hourly_buy": hourly_buy},
                        "lines": [
                            ("Power Score", fmt_no_decimal(power)),
                            ("Coins/saat", fmt_no_decimal(coins_h)),
                            ("Kâr/Item", fmt_no_decimal(margin)),
                            ("Saatlik InstaSell/Buy", f"{fmt_no_decimal(hourly_sell)} / {fmt_no_decimal(hourly_buy)}"),
                            ("Alış/Satış", f"{fmt_no_decimal(buy_p)} / {fmt_no_decimal(sell_p)}"),
                        ]
                    })
                baz = sort_by(self.sort_orders["baz"], "power", baz)[:400]
                def to_cards(data_list, font_scale):
                    return [Card(d["payload"], d["lines"],
                                 on_click=lambda p, m=d["payload"]["mode"]: self.card_clicked(p, m),
                                 font_scale=font_scale,
                                 is_selected=(str(d["payload"].get("id")) in self._selected)) for d in data_list]
                self.tab_baz.populate(to_cards(baz, 1.0), cols=4)

            elif mode == "npc":
                npc = []
                for r in self.raw_rows:
                    if not ok_common(r):
                        continue
                    npc_p = float(r.get("npc_price") or 0)
                    if npc_p <= 0:
                        continue
                    buy_p = float(r["buy_price"])
                    sell_p = float(r["sell_price"])
                    unit = npc_p - buy_p
                    if unit <= 0:
                        continue
                    hourly_sell = int(r.get("hourly_sell", int(r["sell_volume"]) // 24))
                    hourly_buy  = int(r.get("hourly_buy",  int(r["buy_volume"]) // 24))
                    coins_h = unit * min(hourly_sell, hourly_buy)
                    pct = (unit / buy_p) * 100 if buy_p else 0.0
                    if pct < min_pct:
                        continue
                    power = coins_h * max(pct, 1)
                    npc.append({
                        "payload": {**r, "mode":"npc", "power": power, "unit": unit, "coins_h": coins_h,
                                    "hourly_sell": hourly_sell, "hourly_buy": hourly_buy},
                        "lines": [
                            ("Power Score", fmt_no_decimal(power)),
                            ("Coins/saat", fmt_no_decimal(coins_h)),
                            ("Birim Kâr", fmt_no_decimal(unit)),
                            ("Saatlik InstaSell", fmt_no_decimal(hourly_sell)),
                            ("Saatlik InstaBuy", fmt_no_decimal(hourly_buy)),
                            ("NPC", fmt_no_decimal(npc_p)),
                            ("Alış/Satış", f"{fmt_no_decimal(buy_p)} / {fmt_no_decimal(sell_p)}"),
                        ]
                    })
                npc = sort_by(self.sort_orders["npc"], "power", npc)[:400]
                def to_cards(data_list, font_scale):
                    return [Card(d["payload"], d["lines"],
                                 on_click=lambda p, m=d["payload"]["mode"]: self.card_clicked(p, m),
                                 font_scale=font_scale,
                                 is_selected=(str(d["payload"].get("id")) in self._selected)) for d in data_list]
                self.tab_npc.populate(to_cards(npc, 1.0))

            elif mode == "rev":
                rev = []
                for r in self.raw_rows:
                    if not ok_common(r):
                        continue
                    npc_p = float(r.get("npc_price") or 0)
                    if npc_p <= 0:
                        continue
                    sell_p = float(r["sell_price"])
                    buy_p  = float(r["buy_price"])
                    unit = sell_p - npc_p
                    if unit <= 0:
                        continue
                    hourly_buy  = int(r.get("hourly_buy",  int(r["buy_volume"]) // 24))
                    hourly_sell = int(r.get("hourly_sell", int(r["sell_volume"]) // 24))
                    coins_h = unit * min(hourly_buy, hourly_sell)
                    pct = (unit / npc_p) * 100 if npc_p else 0.0
                    if pct < min_pct:
                        continue
                    power = coins_h * max(pct, 1)
                    rev.append({
                        "payload": {**r, "mode":"rev", "power": power, "unit": unit, "coins_h": coins_h,
                                    "hourly_sell": hourly_sell, "hourly_buy": hourly_buy},
                        "lines": [
                            ("Power Score", fmt_no_decimal(power)),
                            ("Coins/saat", fmt_no_decimal(coins_h)),
                            ("Birim Kâr", fmt_no_decimal(unit)),
                            ("Saatlik InstaBuy", fmt_no_decimal(hourly_buy)),
                            ("Saatlik InstaSell", fmt_no_decimal(hourly_sell)),
                            ("NPC", fmt_no_decimal(npc_p)),
                            ("Alış/Satış", f"{fmt_no_decimal(buy_p)} / {fmt_no_decimal(sell_p)}"),
                        ]
                    })
                rev = sort_by(self.sort_orders["rev"], "power", rev)[:400]
                def to_cards(data_list, font_scale):
                    return [Card(d["payload"], d["lines"],
                                 on_click=lambda p, m=d["payload"]["mode"]: self.card_clicked(p, m),
                                 font_scale=font_scale,
                                 is_selected=(str(d["payload"].get("id")) in self._selected)) for d in data_list]
                self.tab_rev.populate(to_cards(rev, 1.0))

        except Exception as e:
            self._log_msg(f"Rebuild error: {e}")

        finally:
            self.setUpdatesEnabled(True)
            self._is_rebuilding = False

    def card_clicked(self, payload, mode):
        name = payload.get("name","?")
        power = fmt_no_decimal(payload.get("power", 0))
        item_id = str(payload.get("id") or "")
        self._log_msg(f"Seçildi: {name} [{mode}] — Power: {power}")
        self._toggle_select(item_id, name)

    def _log_msg(self, msg: str):
        # Safe logging: when log widget isn't ready, print to stdout
        if hasattr(self, 'log') and self.log:
            self.log.append(msg)
        else:
            print(msg)

    # ----- Selected helpers -----
    def _selected_save(self):
        try:
            self._selected_path.parent.mkdir(parents=True, exist_ok=True)
            items = [
                {"id": i, "name": d.get("name", i), "expected_amount": int(d.get("expected_amount", 1))}
                for i,d in sorted(self._selected.items())
            ]
            payload = {
                "items": items,
                "saved_at": int(__import__("time").time())
            }
            self._selected_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            self._log_msg(f"Seçimler kaydedilemedi: {e}")
            return False

    def _selected_load(self):
        try:
            if not self._selected_path.exists():
                return
            data = json.loads(self._selected_path.read_text(encoding="utf-8"))
            # Backward compatible
            out = {}
            if "items" in data:
                for it in (data.get("items") or []):
                    sid = str(it.get("id"))
                    name = str(it.get("name", sid))
                    exp = it.get("expected_amount")
                    if exp is None:
                        # compute from current market rows if available
                        r = self._rows_by_id().get(sid)
                        exp = self._calc_expected_amount(r) if r else 1
                    out[sid] = {"name": name, "expected_amount": int(exp)}
            elif "ids" in data:
                for sid in (data.get("ids") or []):
                    sid = str(sid)
                    out[sid] = {"name": sid, "expected_amount": 1}
            self._selected = out
            self._log_msg(f"Seçimler yüklendi: {len(self._selected)} adet.")
        except Exception as e:
            self._log_msg(f"Seçimler okunamadı: {e}")

    def _update_expected_amount(self, sid: str, new_val: int):
        if sid in self._selected:
            self._selected[sid]["expected_amount"] = max(1, int(new_val))
            self._selected_save()
        # seçim değişti, kartları tazele
        try:
            self._schedule_rebuild()
        except Exception:
            pass
            self._log_msg(f"expected_amount güncellendi: {sid} -> {self._selected[sid]['expected_amount']}")

    def _toggle_select(self, item_id: str, name: str):
        if not item_id:
            return
        if item_id in self._selected:
            del self._selected[item_id]
            self._log_msg(f"Seçim kaldırıldı: {item_id}")
        else:
            # mevcut piyasa verisinden expected_amount öner
            r = self._rows_by_id().get(item_id)
            exp = self._calc_expected_amount(r) if r else 1
            self._selected[item_id] = {"name": name or item_id, "expected_amount": int(exp)}
            self._log_msg(f"Seçildi (kaydedildi): {item_id} -> {self._selected[item_id]}")
        self._selected_save()
        # seçim değişti, kartları tazele
        try:
            self._schedule_rebuild()
        except Exception:
            pass

    def _rows_by_id(self):
        return {str(r.get("id")): r for r in (self.raw_rows or [])}

    def _compute_selected_totals(self):
        rows = self._rows_by_id()
        gross_npc = 0.0
        cost_buy  = 0.0
        for sid, info in self._selected.items():
            r = rows.get(str(sid))
            if not r:
                continue
            try:
                amt = int(info.get("expected_amount", 1))
            except Exception:
                amt = 1
            try:
                buy_p = float(r.get("buy_price", 0) or 0)
            except Exception:
                buy_p = 0.0
            try:
                npc_p = float(r.get("npc_price", 0) or 0)
            except Exception:
                npc_p = 0.0
            cost_buy  += amt * buy_p
            gross_npc += amt * npc_p
        profit = gross_npc - cost_buy
        return cost_buy, gross_npc, profit



    def _delete_selected_item(self, sid, dialog):
        if sid in self._selected:
            del self._selected[sid]
            self._selected_save()
        # seçim değişti, kartları tazele
        try:
            self._schedule_rebuild()
        except Exception:
            pass
            self._log_msg(f"Seçim kaldırıldı: {sid}")
            dialog.accept()
            self._show_selected_dialog()

    def _show_selected_dialog(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QWidget, QSpinBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Seçili Ürünler (Güncel)")
        main_layout = QVBoxLayout(dlg)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vbox = QVBoxLayout(inner)

        by_id = self._rows_by_id()
        if not self._selected:
            vbox.addWidget(QLabel("Seçim yok."))
        else:
            for sid, info in sorted(self._selected.items()):
                disp_name = info.get("name", sid)
                exp_amt = int(info.get("expected_amount", 1))
                r = by_id.get(sid)
                if not r:
                    text = f"{disp_name} [{sid}]: (güncel veri yok)"
                    lbl = QLabel(text)
                    lbl.setWordWrap(True)
                    row = QHBoxLayout()
                    row.addWidget(lbl, 1)
                    btn_del = QPushButton("Sil")
                    btn_del.clicked.connect(lambda _, x=sid: self._delete_selected_item(x, dlg))
                    row.addWidget(btn_del)
                    vbox.addLayout(row)
                else:
                    name = r.get("name", disp_name)
                    buy_p = fmt_no_decimal(r.get("buy_price", 0))
                    sell_p = fmt_no_decimal(r.get("sell_price", 0))
                    npc_p = fmt_no_decimal(r.get("npc_price", 0))
                    hs = fmt_no_decimal(r.get("hourly_sell", 0))
                    hb = fmt_no_decimal(r.get("hourly_buy", 0))
                    text = (f"{name} [{sid}]\n"
                            f"  Alış/Satış: {buy_p} / {sell_p}\n"
                            f"  NPC: {npc_p}\n"
                            f"  Saatlik InstaSell/Buy: {hs} / {hb}")
                    row = QHBoxLayout()
                    lbl = QLabel(text)
                    lbl.setWordWrap(True)
                    row.addWidget(lbl, 1)

                    # expected_amount düzenleme
                    sub = QVBoxLayout()
                    sub.addWidget(QLabel("expected_amount"))
                    spin = QSpinBox()
                    spin.setRange(1, 10_000_000)
                    spin.setValue(exp_amt)
                    sub.addWidget(spin)
                    btn_upd = QPushButton("Güncelle")
                    def _do_upd(_=None, x=sid, w=spin):
                        self._update_expected_amount(x, w.value())
                    btn_upd.clicked.connect(_do_upd)
                    sub.addWidget(btn_upd)

                    btn_del = QPushButton("Sil")
                    btn_del.clicked.connect(lambda _, x=sid: self._delete_selected_item(x, dlg))
                    sub.addWidget(btn_del)

                    row.addLayout(sub)
                    vbox.addLayout(row)

        inner.setLayout(vbox)
        scroll.setWidget(inner)
        main_layout.addWidget(scroll)

        # NPC toplam özet
        try:
            cost_buy, gross_npc, profit = self._compute_selected_totals()
            summary = QLabel(
                f"<b>Özet (NPC Flip)</b><br>"
                f"Toplam Maliyet (Insta Buy): {fmt_no_decimal(cost_buy)}<br>"
                f"NPC Brüt Gelir: {fmt_no_decimal(gross_npc)}<br>"
                f"Net Kâr: {fmt_no_decimal(profit)}"
            )
            summary.setWordWrap(True)
            main_layout.addWidget(summary)
        except Exception as _e:
            # sessizce geç
            pass



        btn_close = QPushButton("Kapat")
        btn_close.clicked.connect(dlg.accept)
        main_layout.addWidget(btn_close)

        dlg.resize(760, 560)
        dlg.exec()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
