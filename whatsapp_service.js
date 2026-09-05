const express = require('express');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const QRCode = require('qrcode');
const axios = require('axios');
const pino = require('pino');
const path = require('path');
const fs = require('fs');

const app = express();
app.use(express.json());

const PORT = 3001;
const AUTH_DIR = path.join(__dirname, 'auth_info');
if (!fs.existsSync(AUTH_DIR)) fs.mkdirSync(AUTH_DIR, { recursive: true });

let latestQR = null;
let isConnected = false;
let connectedUser = null;
let sock = null;

async function startWhatsApp() {
  console.log('[Baileys] تهيئة محرك جلسة الواتساب المشفرة...');
  try {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

    sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['FDC Sales CRM', 'Chrome', '1.0.0']
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        latestQR = await QRCode.toDataURL(qr);
        isConnected = false;
      }

      if (connection === 'close') {
        const shouldReconnect = (lastDisconnect?.error)?.output?.statusCode !== DisconnectReason.loggedOut;
        isConnected = false;
        latestQR = null;
        connectedUser = null;
        if (shouldReconnect) {
          setTimeout(startWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        console.log('✅ [Baileys] تم الاتصال بالواتساب بنجاح وتوثيق الجلسة!');
        isConnected = true;
        latestQR = null;
        connectedUser = sock?.user?.id ? sock.user.id.split(':')[0] : 'متصل';
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        const msg = m.messages[0];
        if (!msg.message || msg.key.fromMe) return;

        const chatId = msg.key.remoteJid;
        const senderPhone = (msg.key.participant || chatId).split('@')[0];
        const senderName = msg.pushName || senderPhone;
        const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';

        if (!text) return;

        await axios.post('http://127.0.0.1:8000/api/whatsapp/webhook', {
          chat_id: chatId,
          sender_phone: `+${senderPhone}`,
          sender_name: senderName,
          message_text: text
        });
      } catch (e) {}
    });

  } catch (err) {
    setTimeout(startWhatsApp, 5000);
  }
}

// مسار فحص الحالة
app.get('/qr-status', (req, res) => {
  res.json({
    connected: isConnected,
    user: connectedUser,
    qr: latestQR
  });
});

// أداة استخراج المجموعات
app.get('/groups', async (req, res) => {
  if (!isConnected || !sock) {
    return res.json([]);
  }
  try {
    const groups = await sock.groupFetchAllParticipating();
    const result = Object.values(groups).map(g => ({
      id: g.id,
      subject: g.subject,
      participants_count: g.participants?.length || 0
    }));
    res.json(result);
  } catch (e) {
    res.json([]);
  }
});

// مسار إرسال الرسائل الصادرة للوكلاء الأذكياء واختبارات النظام
app.post('/send-message', async (req, res) => {
  if (!isConnected || !sock) {
    return res.status(503).json({ error: 'جلسة الواتساب غير متصلة حالياً' });
  }

  const { phone_or_group, message } = req.body;
  if (!phone_or_group || !message) {
    return res.status(400).json({ error: 'يرجى تحديد الرقم والرسالة' });
  }

  try {
    let cleanTarget = phone_or_group.replace(/[^0-9@a-z\._-]/gi, '');
    let jid = '';

    if (cleanTarget.endsWith('@g.us')) {
      jid = cleanTarget;
    } else {
      if (cleanTarget.startsWith('+')) cleanTarget = cleanTarget.substring(1);
      jid = `${cleanTarget}@s.whatsapp.net`;
    }

    await sock.sendMessage(jid, { text: message });
    console.log(`[Baileys Sent] تم إرسال رسالة بنجاح إلى ${jid}`);
    return res.json({ status: 'SENT', to: jid });
  } catch (e) {
    console.error('[Baileys Send Error]:', e);
    return res.status(500).json({ error: e.message });
  }
});

startWhatsApp();

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Express] Listening internally on 127.0.0.1:${PORT}`);
});
