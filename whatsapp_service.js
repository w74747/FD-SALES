/**
 * whatsapp_service.js - Multi-Session WhatsApp Engine
 * Ultra-stable native Baileys sessions for Railway deployment
 * Session 1 (operations): Group monitoring, smart dispatch & notifications
 * Session 2 (sales): Inbound new customer sales bot & auto-qualification
 */

const express = require('express');
const { 
  default: makeWASocket, 
  DisconnectReason, 
  useMultiFileAuthState 
} = require('@whiskeysockets/baileys');
const QRCode = require('qrcode');
const axios = require('axios');
const pino = require('pino');
const fs = require('fs');
const path = require('path');

process.on('uncaughtException', (err) => {
  console.error('[Baileys UncaughtException]:', err.message);
});
process.on('unhandledRejection', (reason) => {
  console.error('[Baileys UnhandledRejection]:', reason);
});

const app = express();
app.use(express.json());

const PORT = 3001;

// كائنات تخزين الجلسات المزدوجة
const sessions = {
  operations: { sock: null, qr: null, connected: false, user: null, isStarting: false },
  sales: { sock: null, qr: null, connected: false, user: null, isStarting: false }
};

// ----------------- 1. تشغيل جلسة العمليات (Operations) -----------------
async function startOperationsWhatsApp() {
  if (sessions.operations.isStarting) return;
  sessions.operations.isStarting = true;

  try {
    const authFolder = path.join(__dirname, 'auth_info');
    if (!fs.existsSync(authFolder)) fs.mkdirSync(authFolder, { recursive: true });

    const { state, saveCreds } = await useMultiFileAuthState(authFolder);

    if (sessions.operations.sock) {
      try { sessions.operations.sock.ev.removeAllListeners(); } catch (e) {}
    }

    const sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['FDC Operations CRM', 'Chrome', '11.0.0']
    });

    sessions.operations.sock = sock;
    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        try {
          sessions.operations.qr = await QRCode.toDataURL(qr);
          sessions.operations.connected = false;
        } catch (err) {}
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
        sessions.operations.connected = false;
        sessions.operations.qr = null;
        sessions.operations.isStarting = false;

        if (shouldReconnect) {
          setTimeout(startOperationsWhatsApp, 3000);
        } else {
          try { fs.rmSync(authFolder, { recursive: true, force: true }); } catch (e) {}
          setTimeout(startOperationsWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.operations.connected = true;
        sessions.operations.qr = null;
        const cleanPhone = sock?.user?.id ? sock.user.id.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
        sessions.operations.user = cleanPhone;
        sessions.operations.isStarting = false;
        console.log('[Operations WA] تم الاتصال بنجاح بالرقم:', cleanPhone);
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        const msg = m.messages[0];
        if (!msg || !msg.message) return;

        const chatId = msg.key.remoteJid;
        const fromMe = Boolean(msg.key.fromMe);
        const myPhoneNumber = sessions.operations.user || '';
        const isSelfChat = chatId.includes(myPhoneNumber);

        if (fromMe && !isSelfChat) return;

        const senderPhone = (msg.key.participant || chatId).split('@')[0];
        const senderName = msg.pushName || senderPhone;
        const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';
        if (!text) return;

        const resp = await axios.post('http://127.0.0.1:8000/api/whatsapp/webhook', {
          chat_id: chatId,
          sender_phone: `+${senderPhone.replace(/^\+/, '')}`,
          sender_name: senderName,
          message_text: text
        }, { timeout: 6000 });

        if (resp.data && resp.data.reply_text) {
          await sock.sendMessage(chatId, { text: resp.data.reply_text });
        }

        if (resp.data && resp.data.forward_to_logistics && resp.data.logistics_text) {
          const logJid = resp.data.forward_to_logistics.includes('@g.us') 
            ? resp.data.forward_to_logistics 
            : `${resp.data.forward_to_logistics.replace(/[^0-9]/g, '')}@s.whatsapp.net`;
          await sock.sendMessage(logJid, { text: resp.data.logistics_text });
        }
      } catch (e) {}
    });

  } catch (err) {
    sessions.operations.isStarting = false;
    setTimeout(startOperationsWhatsApp, 5000);
  }
}

// ----------------- 2. تشغيل جلسة مبيعات العملاء الجدد (Sales) -----------------
async function startSalesWhatsApp() {
  if (sessions.sales.isStarting) return;
  sessions.sales.isStarting = true;

  try {
    const authFolder = path.join(__dirname, 'auth_sales');
    if (!fs.existsSync(authFolder)) fs.mkdirSync(authFolder, { recursive: true });

    const { state, saveCreds } = await useMultiFileAuthState(authFolder);

    if (sessions.sales.sock) {
      try { sessions.sales.sock.ev.removeAllListeners(); } catch (e) {}
    }

    const sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['FDC Inbound Sales', 'Chrome', '1.0.0']
    });

    sessions.sales.sock = sock;
    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        try {
          sessions.sales.qr = await QRCode.toDataURL(qr);
          sessions.sales.connected = false;
        } catch (err) {}
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
        sessions.sales.connected = false;
        sessions.sales.qr = null;
        sessions.sales.isStarting = false;

        if (shouldReconnect) {
          setTimeout(startSalesWhatsApp, 3000);
        } else {
          try { fs.rmSync(authFolder, { recursive: true, force: true }); } catch (e) {}
          setTimeout(startSalesWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.sales.connected = true;
        sessions.sales.qr = null;
        const cleanPhone = sock?.user?.id ? sock.user.id.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
        sessions.sales.user = cleanPhone;
        sessions.sales.isStarting = false;
        console.log('[Sales WA] تم الاتصال بنجاح بالرقم:', cleanPhone);
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        const msg = m.messages[0];
        if (!msg || !msg.message || msg.key.fromMe) return;

        const chatId = msg.key.remoteJid;
        if (chatId.endsWith('@g.us')) return; // تجاهل المجموعات، التركيز على محادثات العملاء الأفراد

        const senderPhone = chatId.split('@')[0];
        const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';
        if (!text) return;

        const resp = await axios.post('http://127.0.0.1:8000/api/bot/inbound-sales', {
          sender_phone: `+${senderPhone.replace(/^\+/, '')}`,
          sender_name: msg.pushName || 'عميل جديد',
          message_text: text
        }, { timeout: 8000 });

        if (resp.data && resp.data.reply_text) {
          await sock.sendMessage(chatId, { text: resp.data.reply_text });
        }
      } catch (e) {}
    });

  } catch (err) {
    sessions.sales.isStarting = false;
    setTimeout(startSalesWhatsApp, 5000);
  }
}

// ----------------- مسارات API للتحكم بالجلسات -----------------

// حالة جلسة العمليات
app.get('/qr-status', (req, res) => {
  res.json({
    connected: sessions.operations.connected,
    user: sessions.operations.user,
    qr: sessions.operations.qr
  });
});

// حالة جلسة مبيعات العملاء الجدد
app.get('/sales/qr-status', (req, res) => {
  res.json({
    connected: sessions.sales.connected,
    user: sessions.sales.user,
    qr: sessions.sales.qr
  });
});

// جلب مجموعات حساب العمليات
app.get('/groups', async (req, res) => {
  if (!sessions.operations.connected || !sessions.operations.sock) return res.json([]);
  try {
    const groups = await sessions.operations.sock.groupFetchAllParticipating();
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

// إرسال رسائل من الجلسة المحددة
app.post('/send-message', async (req, res) => {
  const { phone_or_group, message, session_type } = req.body;
  const activeSession = (session_type === 'sales' && sessions.sales.connected) 
    ? sessions.sales 
    : sessions.operations;

  if (!activeSession.connected || !activeSession.sock) {
    return res.status(503).json({ error: 'خدمة الواتساب المطلوبة غير متصلة حالياً' });
  }

  try {
    let cleanTarget = phone_or_group.replace(/[^0-9@a-z._-]/gi, '');
    let jid = cleanTarget.endsWith('@g.us') ? cleanTarget : `${cleanTarget.replace(/^\+/, '')}@s.whatsapp.net`;

    await activeSession.sock.sendMessage(jid, { text: message });
    return res.json({ status: 'SENT', to: jid });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

// فصل جلسة العمليات
app.post('/disconnect', async (req, res) => {
  try {
    sessions.operations.connected = false;
    sessions.operations.user = null;
    sessions.operations.qr = null;
    if (sessions.operations.sock) {
      try { await sessions.operations.sock.logout(); } catch (e) {}
      try { sessions.operations.sock.end(); } catch (e) {}
    }
    const authFolder = path.join(__dirname, 'auth_info');
    try { fs.rmSync(authFolder, { recursive: true, force: true }); } catch (e) {}
    setTimeout(startOperationsWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

// فصل جلسة المبيعات
app.post('/sales/disconnect', async (req, res) => {
  try {
    sessions.sales.connected = false;
    sessions.sales.user = null;
    sessions.sales.qr = null;
    if (sessions.sales.sock) {
      try { await sessions.sales.sock.logout(); } catch (e) {}
      try { sessions.sales.sock.end(); } catch (e) {}
    }
    const authFolder = path.join(__dirname, 'auth_sales');
    try { fs.rmSync(authFolder, { recursive: true, force: true }); } catch (e) {}
    setTimeout(startSalesWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

// إطلاق الجلستين معاً في الخلفية
startOperationsWhatsApp();
startSalesWhatsApp();

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Multi-Session Server] شغال ويستمع على 127.0.0.1:${PORT}`);
});
