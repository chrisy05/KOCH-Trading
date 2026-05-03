// TradingView Webhook → Telegram Forwarder
// Deployed auf Vercel als Serverless Function

export default async function handler(req, res) {
  // Nur POST akzeptieren
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Only POST allowed' });
  }

  // TradingView sendet den Alert-Text als Body
  const body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
  
  // Konfiguration
  const TELEGRAM_TOKEN = '8663941433:AAHIz1GCUkDgljagaNZ0XfHIExf2ihfRvnA';
  const SIGNAL_CHANNEL = '-1003770314055';
  
  // Optional: Secret Key für Sicherheit (in TV Alert Message mitsenden)
  const expectedKey = process.env.WEBHOOK_SECRET || 'HEMI_V8_KODA';
  
  try {
    // Parse den Alert-Text
    let alertText = body;
    
    // Wenn JSON, extrahiere die Message
    try {
      const parsed = JSON.parse(body);
      alertText = parsed.message || parsed.text || JSON.stringify(parsed);
    } catch (e) {
      // Body ist plain text — OK
    }

    // Sicherheitscheck (optional)
    if (!alertText.includes(expectedKey) && !alertText.includes('HEMI') && !alertText.includes('ER1')) {
      return res.status(403).json({ error: 'Invalid webhook source' });
    }

    // Formatiere Telegram Nachricht
    const telegramMsg = `🔔 TradingView Alert\n\n${alertText}\n\n⏰ ${new Date().toISOString().slice(0,19)}`;

    // Sende an Telegram
    const tgUrl = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`;
    const tgResponse = await fetch(tgUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: SIGNAL_CHANNEL,
        text: telegramMsg,
      }),
    });

    const tgResult = await tgResponse.json();

    if (tgResult.ok) {
      return res.status(200).json({ success: true, message_id: tgResult.result.message_id });
    } else {
      return res.status(500).json({ error: 'Telegram send failed', details: tgResult });
    }
  } catch (error) {
    return res.status(500).json({ error: error.message });
  }
}
