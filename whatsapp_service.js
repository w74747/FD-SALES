const express = require('express');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const QRCode = require('qrcode');
const axios = require('axios');
const pino = require('pino');
const path = require('path');
const fs = require('fs');

const app = express();
app.use(express.json());

const PORT = 3001; // منفذ داخلي خاص بخدمة الواتساب
const AUTH_DIR = path.join(__dirname, 'auth_info');
if (!fs.existsSync(AUTH_DIR)) fs.mkdirSync(AUTH_DIR, { recursive: true });

let latestQR = null;
let isConnected = false;
let sock = null;

async function startWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  sock = makeWASocket({
    auth: state,
    logger: pino({ level: 'silent' }),
    printQRInTerminal: false
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
      if (shouldReconnect) {
        startWhatsApp();
      }
    } else if (connection === 'open') {
      isConnected = true;
      latestQR = null;
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

      // تمرير الرسالة مباشرة إلى FastAPI داخلياً
      await axios.post('http://127.0.0.1:8000/api/whatsapp/webhook', {
        chat_id: chatId,
        sender_phone: `+${senderPhone}`,
        sender_name: senderName,
        message_text: text
      });
    } catch (e) {
      // تجاهل الأخطاء العابرة
    }
  });
}

app.get('/qr-status', (req, res) => {
  res.json({
    connected: isConnected,
    qr: latestQR
  });
});

startWhatsApp();
app.listen(PORT, '127.0.0.1', () => {
  console.log(`WhatsApp internal service running on port ${PORT}`);
});
