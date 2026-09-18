/**
 * whatsapp_service.js - Resilient Multi-Session WhatsApp Engine
 * Backed by PostgreSQL Database Auth State to Prevent Disconnections across Deployments
 * Food Development Company (شركة تنمية الغذاء)
 */

const express = require('express');
const { 
  default: makeWASocket, 
  DisconnectReason, 
  fetchLatestBaileysVersion,
  BufferJSON,
  initAuthCreds
} = require('@whiskeysockets/baileys');
const QRCode = require('qrcode');
const axios = require('axios');
const pino = require('pino');

process.on('uncaughtException', (err) => {
  console.error('[Baileys UncaughtException]:', err.message);
});
process.on('unhandledRejection', (reason) => {
  console.error('[Baileys UnhandledRejection]:', reason);
});

const app = express();
app.use(express.json());

const PORT = 3001;

// ----------------- محول تخزين الجلسة الدائم في قاعدة البيانات -----------------
async function useDatabaseAuthState(sessionId) {
  // قراءة مفتاح محدد من قاعدة البيانات
  const readData = async (keyId) => {
    try {
      const res = await axios.get(`http://127.0.0.1:8000/api/internal/auth-store/${sessionId}/${encodeURIComponent(keyId)}`, { timeout: 3000 });
      if (res.data && res.data.key_data) {
        return JSON.parse(res.data.key_data, BufferJSON.reviver);
      }
    } catch (e) {}
    return null;
  };

  // كتابة مفتاح في قاعدة البيانات
  const writeData = async (keyId, data) => {
    try {
      const serialized = JSON.stringify(data, BufferJSON.replacer);
      await axios.post('http://127.0.0.1:8000/api/internal/auth-store', {
        session_id: sessionId,
        key_id: keyId,
        key_data: serialized
      }, { timeout: 3000 });
    } catch (e) {}
  };

  // حذف مفتاح من قاعدة البيانات
  const removeData = async (keyId) => {
    try {
      await axios.delete(`http://127.0.0.1:8000/api/internal/auth-store/${sessionId}/${encodeURIComponent(keyId)}`, { timeout: 3000 });
    } catch (e) {}
  };

  const creds = (await readData('creds')) || initAuthCreds();

  return {
    state: {
      creds,
      keys: {
        get: async (type, ids) => {
          const data = {};
          await Promise.all(
            ids.map(async (id) => {
              let value = await readData(`${type}-${id}`);
              if (type === 'app-state-sync-key' && value) {
                value = BufferJSON.reviver(value);
              }
              if (value) {
                data[id] = value;
              }
            })
          );
          return data;
        },
        set: async (data) => {
          const tasks = [];
          for (const category in data) {
            for (const id in data[category]) {
              const val = data[category][id];
              const key = `${category}-${id}`;
              tasks.push(val ? writeData(key, val) : removeData(key));
            }
          }
          await Promise.all(tasks);
        }
      }
    },
    saveCreds: () => writeData('creds', creds)
  };
}

const sessions = {
  operations: { sock: null, qr: null, connected: false, user: null, isStarting: false },
  sales: { sock: null, qr: null, connected: false, user: null, isStarting: false }
};

const messageStore = new Map();

// ----------------- 1. تشغيل جلسة العمليات الرئيسية الدائمة -----------------
async function startOperationsWhatsApp() {
  if (sessions.operations.isStarting) return;
  sessions.operations.isStarting = true;

  try {
    const { state, saveCreds } = await useDatabaseAuthState('operations_main');
    const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1015901307] }));

    if (sessions.operations.sock) {
      try { sessions.operations.sock.ev.removeAllListeners(); } catch (e) {}
    }

    const sock = makeWASocket({
      version,
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['FDC Operations CRM', 'Chrome', '120.0.0'],
      syncFullHistory: false,
      markOnlineOnConnect: true,
      connectTimeoutMs: 60000,
      keepAliveIntervalMs: 25000,
      getMessage: async (key) => messageStore.get(key.id) || undefined
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

        console.log(`[Operations WA] Closed. Code: ${statusCode}. Reconnecting: ${shouldReconnect}`);

        if (shouldReconnect) {
          setTimeout(startOperationsWhatsApp, 3000);
        } else {
          // في حال تسجيل الخروج الصريح فقط يتم مسح بيانات الجلسة من قاعدة البيانات
          try {
            await axios.delete('http://127.0.0.1:8000/api/internal/auth-store/operations_main');
          } catch (e) {}
          setTimeout(startOperationsWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.operations.connected = true;
        sessions.operations.qr = null;
        const cleanPhone = sock?.user?.id ? sock.user.id.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
        sessions.operations.user = cleanPhone;
        sessions.operations.isStarting = false;
        console.log('[Operations WA] Persistent Session Restored & Active on Number:', cleanPhone);
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        if (!m.messages || m.messages.length === 0) return;
        const msg = m.messages[0];
        if (!msg.message) return;

        if (msg.key && msg.key.id) {
          messageStore.set(msg.key.id, msg.message);
          if (messageStore.size > 200) {
            const first = messageStore.keys().next().value;
            messageStore.delete(first);
          }
        }

        const chatId = msg.key.remoteJid;
        const text = msg.message.conversation || 
                     msg.message.extendedTextMessage?.text || 
                     msg.message.imageMessage?.caption || 
                     '';
        if (!text.trim()) return;

        const senderPhone = (msg.key.participant || chatId).split('@')[0].replace(/[^0-9]/g, '');
        const senderName = msg.pushName || senderPhone;

        const resp = await axios.post('http://127.0.0.1:8000/api/whatsapp/webhook', {
          chat_id: chatId,
          sender_phone: `+${senderPhone}`,
          sender_name: senderName,
          message_text: text
        }, { timeout: 8000 });

        if (resp.data && resp.data.reply_text) {
          await sock.sendMessage(chatId, { text: resp.data.reply_text });
        }

        if (resp.data && resp.data.forward_to_logistics && resp.data.logistics_text) {
          let logJid = resp.data.forward_to_logistics.trim();
          if (!logJid.endsWith('@g.us') && !logJid.endsWith('@s.whatsapp.net')) {
            logJid = `${logJid}@g.us`;
          }
          await sock.sendMessage(logJid, { text: resp.data.logistics_text });
        }
      } catch (e) {}
    });

  } catch (err) {
    sessions.operations.isStarting = false;
    setTimeout(startOperationsWhatsApp, 5000);
  }
}

// ----------------- 2. تشغيل جلسة مبيعات العملاء الجدد الدائمة -----------------
async function startSalesWhatsApp() {
  if (sessions.sales.isStarting) return;
  sessions.sales.isStarting = true;

  try {
    const { state, saveCreds } = await useDatabaseAuthState('sales_inbound');
    const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1015901307] }));

    if (sessions.sales.sock) {
      try { sessions.sales.sock.ev.removeAllListeners(); } catch (e) {}
    }

    const sock = makeWASocket({
      version,
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['FDC Inbound Sales', 'Chrome', '120.0.0'],
      syncFullHistory: false,
      markOnlineOnConnect: true,
      connectTimeoutMs: 60000,
      keepAliveIntervalMs: 25000
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
          try {
            await axios.delete('http://127.0.0.1:8000/api/internal/auth-store/sales_inbound');
          } catch (e) {}
          setTimeout(startSalesWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.sales.connected = true;
        sessions.sales.qr = null;
        const cleanPhone = sock?.user?.id ? sock.user.id.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
        sessions.sales.user = cleanPhone;
        sessions.sales.isStarting = false;
        console.log('[Sales WA] Inbound Bot Session Restored on Number:', cleanPhone);
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        if (!m.messages || m.messages.length === 0) return;
        const msg = m.messages[0];
        if (!msg.message || msg.key.fromMe) return;

        const chatId = msg.key.remoteJid;
        if (chatId.endsWith('@g.us')) return;

        const senderPhone = chatId.split('@')[0].replace(/[^0-9]/g, '');
        const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';
        if (!text.trim()) return;

        const resp = await axios.post('http://127.0.0.1:8000/api/bot/inbound-sales', {
          sender_phone: `+${senderPhone}`,
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

// ----------------- مسارات الـ API للتحكم -----------------
app.get('/qr-status', (req, res) => {
  res.json({
    connected: sessions.operations.connected,
    user: sessions.operations.user,
    qr: sessions.operations.qr
  });
});

app.get('/sales/qr-status', (req, res) => {
  res.json({
    connected: sessions.sales.connected,
    user: sessions.sales.user,
    qr: sessions.sales.qr
  });
});

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

app.post('/disconnect', async (req, res) => {
  try {
    sessions.operations.connected = false;
    sessions.operations.user = null;
    sessions.operations.qr = null;
    if (sessions.operations.sock) {
      try { await sessions.operations.sock.logout(); } catch (e) {}
      try { sessions.operations.sock.end(); } catch (e) {}
    }
    try {
      await axios.delete('http://127.0.0.1:8000/api/internal/auth-store/operations_main');
    } catch (e) {}
    setTimeout(startOperationsWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

app.post('/sales/disconnect', async (req, res) => {
  try {
    sessions.sales.connected = false;
    sessions.sales.user = null;
    sessions.sales.qr = null;
    if (sessions.sales.sock) {
      try { await sessions.sales.sock.logout(); } catch (e) {}
      try { sessions.sales.sock.end(); } catch (e) {}
    }
    try {
      await axios.delete('http://127.0.0.1:8000/api/internal/auth-store/sales_inbound');
    } catch (e) {}
    setTimeout(startSalesWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

// بدء الاتصال عند تشغيل الخادم
setTimeout(() => {
  startOperationsWhatsApp();
  startSalesWhatsApp();
}, 2000);

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Multi-Session Server] PostgreSQL-backed engine listening on 127.0.0.1:${PORT}`);
});
