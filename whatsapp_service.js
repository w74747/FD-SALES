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
      printQRInTerminal: false
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log('[Baileys] تم توليد رمز QR حقيقي جديد.');
        latestQR = await QRCode.toDataURL(qr);
        isConnected = false;
      }

      if (connection === 'close') {
        const shouldReconnect = (lastDisconnect?.error)?.output?.statusCode !== DisconnectReason.loggedOut;
        console.log('[Baileys] انقطع الاتصال، إعادة المحاولة:', shouldReconnect);
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
      } catch (e) {
        console.error('[Baileys Webhook Error]:', e.message);
      }
    });

  } catch (err) {
    console.error('[Baileys Fatal Error]:', err);
    setTimeout(startWhatsApp, 5000);
  }
}

app.get('/qr-status', (req, res) => {
  res.json({
    connected: isConnected,
    user: connectedUser,
    qr: latestQR
  });
});

startWhatsApp();

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Express] Listening internally on 127.0.0.1:${PORT}`);
});
