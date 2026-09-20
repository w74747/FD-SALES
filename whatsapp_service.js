/**
 * whatsapp_service.js - High-Speed WhatsApp Engine with Database Snapshot Persistence
 * Food Development Company (شركة تنمية الغذاء)
 * Uses native ultra-fast local auth state for instant pairing + Auto DB Snapshot on Postgres
 */

const express = require('express');
const { 
  default: makeWASocket, 
  DisconnectReason, 
  fetchLatestBaileysVersion,
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
app.use(express.json({ limit: '50mb' }));

const PORT = 3001;

// ----------------- دوال المزامنة التلقائية مع PostgreSQL -----------------
function snapshotFolder(folderPath) {
  const snapshot = {};
  if (!fs.existsSync(folderPath)) return snapshot;
  const files = fs.readdirSync(folderPath);
  for (const file of files) {
    const fullPath = path.join(folderPath, file);
    try {
      if (fs.statSync(fullPath).isFile()) {
        snapshot[file] = fs.readFileSync(fullPath, 'utf8');
      }
    } catch (e) {}
  }
  return snapshot;
}

function restoreFolder(folderPath, snapshot) {
  if (!fs.existsSync(folderPath)) fs.mkdirSync(folderPath, { recursive: true });
  for (const [file, content] of Object.entries(snapshot)) {
    try {
      fs.writeFileSync(path.join(folderPath, file), content, 'utf8');
    } catch (e) {}
  }
}

async function restoreSessionFromDB(sessionName, folderPath) {
  try {
    const res = await axios.get(`http://127.0.0.1:8000/api/internal/session-snapshot/${sessionName}`, { timeout: 4000 });
    if (res.data && res.data.snapshot && Object.keys(res.data.snapshot).length > 0) {
      restoreFolder(folderPath, res.data.snapshot);
      console.log(`[DB Restore] Successfully restored session '${sessionName}' from PostgreSQL.`);
      return true;
    }
  } catch (e) {}
  return false;
}

let saveTimeout = null;
function debouncedSaveSessionToDB(sessionName, folderPath) {
  clearTimeout(saveTimeout);
  saveTimeout = setTimeout(async () => {
    try {
      const snapshot = snapshotFolder(folderPath);
      if (Object.keys(snapshot).length > 0) {
        await axios.post('http://127.0.0.1:8000/api/internal/session-snapshot', {
          session_name: sessionName,
          snapshot: snapshot
        }, { timeout: 6000 });
        console.log(`[DB Backup] Session '${sessionName}' securely synced to PostgreSQL.`);
      }
    } catch (e) {}
  }, 2000);
}

const sessions = {
  operations: { sock: null, qr: null, connected: false, user: null, isStarting: false },
  sales: { sock: null, qr: null, connected: false, user: null, isStarting: false }
};

const messageStore = new Map();

// ----------------- 1. تشغيل واتساب العمليات الرئيسي -----------------
async function startOperationsWhatsApp() {
  if (sessions.operations.isStarting) return;
  sessions.operations.isStarting = true;

  const authFolder = path.join(__dirname, 'auth_info');
  await restoreSessionFromDB('operations_main', authFolder);

  try {
    const { state, saveCreds } = await useMultiFileAuthState(authFolder);
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

    sock.ev.on('creds.update', () => {
      saveCreds();
      debouncedSaveSessionToDB('operations_main', authFolder);
    });

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
          try {
            fs.rmSync(authFolder, { recursive: true, force: true });
            await axios.delete('http://127.0.0.1:8000/api/internal/session-snapshot/operations_main');
          } catch (e) {}
          setTimeout(startOperationsWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.operations.connected = true;
        sessions.operations.qr = null;
        const cleanPhone = sock?.user?.id ? sock.user.id.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
        sessions.operations.user = cleanPhone;
        sessions.operations.isStarting = false;
        console.log('[Operations WA] Successfully Connected & Active on:', cleanPhone);
        debouncedSaveSessionToDB('operations_main', authFolder);
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
            const firstKey = messageStore.keys().next().value;
            messageStore.delete(firstKey);
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

// ----------------- 2. تشغيل واتساب مبيعات العملاء الجدد -----------------
async function startSalesWhatsApp() {
  if (sessions.sales.isStarting) return;
  sessions.sales.isStarting = true;

  const authFolder = path.join(__dirname, 'auth_sales');
  await restoreSessionFromDB('sales_inbound', authFolder);

  try {
    const { state, saveCreds } = await useMultiFileAuthState(authFolder);
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

    sock.ev.on('creds.update', () => {
      saveCreds();
      debouncedSaveSessionToDB('sales_inbound', authFolder);
    });

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
            fs.rmSync(authFolder, { recursive: true, force: true });
            await axios.delete('http://127.0.0.1:8000/api/internal/session-snapshot/sales_inbound');
          } catch (e) {}
          setTimeout(startSalesWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.sales.connected = true;
        sessions.sales.qr = null;
        const cleanPhone = sock?.user?.id ? sock.user.id.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
        sessions.sales.user = cleanPhone;
        sessions.sales.isStarting = false;
        console.log('[Sales WA] Inbound Bot Connected on:', cleanPhone);
        debouncedSaveSessionToDB('sales_inbound', authFolder);
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

// ----------------- مسارات الـ API -----------------
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
    return res.status(503).json({ error: 'خدمة الواتساب غير متصلة' });
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
    const authFolder = path.join(__dirname, 'auth_info');
    try { fs.rmSync(authFolder, { recursive: true, force: true }); } catch (e) {}
    try { await axios.delete('http://127.0.0.1:8000/api/internal/session-snapshot/operations_main'); } catch (e) {}
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
    const authFolder = path.join(__dirname, 'auth_sales');
    try { fs.rmSync(authFolder, { recursive: true, force: true }); } catch (e) {}
    try { await axios.delete('http://127.0.0.1:8000/api/internal/session-snapshot/sales_inbound'); } catch (e) {}
    setTimeout(startSalesWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

// بدء التشغيل
setTimeout(() => {
  startOperationsWhatsApp();
  startSalesWhatsApp();
}, 2500);

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Multi-Session Server] Instant pairing engine listening on 127.0.0.1:${PORT}`);
});
