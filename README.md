
# Bazaar Pro (Modüler)

- **Collect & Sell** döngüsü artık `app/services/collect_service.py` altında bağımsız bir servis.
- UI karanlık tema ve hover efektleri ile güncellendi (`app/ui/styles/dark.qss`).
- Global **F1** kısayolu ile collect&sell başlat/durdur.
- Ayarlar `app/data/config.json` içinden (FastSell interval).
- Koordinatlar `app/data/coordinates.json`.

## Çalıştırma


pip install -r requirements.txt



python -m app.ui.main


> Not: `app/data/template/green.png` şablonu boş placeholder olarak eklendi. Kendi şablon görselinizi bu dosya ile değiştirin.

## Dizim

```
app/
  bazaar.py
  fastsell.py
  workers.py
  data/
    config.json
    coordinates.json
    template/green.png
  services/
    collect_service.py
  ui/
    styles/dark.qss
    main.py
```
