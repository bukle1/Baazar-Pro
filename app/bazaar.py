import time
from datetime import datetime
from pathlib import Path
import requests

class Bazaar:
    BAZAAR_URL = "https://api.hypixel.net/v2/skyblock/bazaar"
    ITEMS_URL  = "https://api.hypixel.net/resources/skyblock/items"

    def fetch_bazaar(self) -> dict:
        r = requests.get(self.BAZAAR_URL, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError("Bazaar API 'success' false")
        return data.get("products", {})

    def fetch_items_meta(self) -> dict:
        r = requests.get(self.ITEMS_URL, timeout=15)
        r.raise_for_status()
        data = r.json()
        out = {}
        for it in data.get("items", []):
            item_id = it.get("id")
            if not item_id:
                continue
            out[item_id] = {
                "name": it.get("name", item_id),
                "tier": it.get("tier", "UNKNOWN"),
                "category": it.get("category", "Unknown"),
                "npc_price": it.get("npc_sell_price") or it.get("npc_buy_price") or 0.0
            }
        return out

    def analyze_bazaar(self):
        ts = int(time.time())
        iso = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        products = self.fetch_bazaar()
        meta = self.fetch_items_meta()

        rows = []
        for item_id, body in products.items():
            buy_summary = body.get("buy_summary") or []
            sell_summary = body.get("sell_summary") or []

            insta_buy  = float(sell_summary[0]["pricePerUnit"]) if sell_summary else 0.0  # pay to buy instantly
            insta_sell = float(buy_summary[0]["pricePerUnit"])  if buy_summary  else 0.0  # get when selling instantly

            sell_vol = int(sum(x.get("amount", 0) for x in sell_summary[:10]))  # supply
            buy_vol  = int(sum(x.get("amount", 0) for x in buy_summary[:10]))   # demand

            # Convert to per-hour estimates (API volumes are ~24h)
            hourly_sell = sell_vol // 24
            hourly_buy  = buy_vol  // 24

            info = meta.get(item_id, {})
            name = info.get("name", item_id)
            npc_price = float(info.get("npc_price") or 0.0)
            category = info.get("category", "Unknown")
            tier = info.get("tier", "UNKNOWN")

            if insta_buy <= 0 or insta_sell <= 0:
                continue

            spread = insta_sell - insta_buy
            spread_pct = (spread / insta_buy) * 100 if insta_buy else 0.0
            npc_unit  = npc_price - insta_buy      # Pazar->NPC
            rev_unit  = insta_sell - npc_price     # NPC->Pazar

            # her ürün için satır oluştur
            row = {
                "timestamp": ts, "iso": iso,
                "id": item_id, "name": name,
                "buy_price": round(insta_buy, 2),
                "sell_price": round(insta_sell, 2),
                "npc_price": round(npc_price, 2),
                "sell_volume": sell_vol,
                "buy_volume": buy_vol,
                "hourly_sell": hourly_sell,
                "hourly_buy": hourly_buy,
                "spread": round(spread, 2),
                "spread_percent": round(spread_pct, 2),
                "npc_unit": round(npc_unit, 2),
                "rev_unit": round(rev_unit, 2),
                "category": category,
                "tier": tier,
            }
            rows.append(row)

        return rows

