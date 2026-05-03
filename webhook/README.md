# KODA TradingView Webhook → Telegram

Empfängt TradingView Alert Webhooks und leitet sie an den Telegram Signalkanal weiter.

## Setup

1. Auf vercel.com einloggen (mit GitHub)
2. "Import Project" → dieses Repo auswählen → Ordner "webhook"
3. Deploy klicken
4. URL kopieren: `https://koda-tv-webhook.vercel.app/api/tv-webhook`
5. In TradingView Alert: Webhook URL eintragen

## TradingView Alert Message Format

```
HEMI V8_1 Entry
{{ticker}} | {{close}}
Setup: {{strategy.order.action}}
```

## Test

```bash
curl -X POST https://deine-url.vercel.app/api/tv-webhook \
  -H "Content-Type: text/plain" \
  -d "HEMI V8_1 Entry\nDOGEUSDT | 0.1084\nSetup A: SHORT"
```
