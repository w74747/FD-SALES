/**
 * whatsapp_service.js - Multi-Session WhatsApp Engine
 * Dual-session persistent storage via PostgreSQL for Railway deployment
 * Session 1 (operations): Group monitoring, smart dispatch & notifications
 * Session 2 (sales): Inbound new customer sales bot & auto-qualification
 */

const express = require('express');
const { 
  default: makeWASocket, 
  DisconnectReason, 
  BufferJSON, 
  initAuthCreds, 
  proto,
  useMultiFileAuthState 
} = require('@whiskeysockets/baileys');
const QRCode = require('qrcode');
const axios = require('axios');
const pino = require('pino');
const { Pool } = require('pg');
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
const DATABASE_URL = process.env.DATABASE_URL || process.env.DATABASE_PUBLIC_URL || process.env.POSTGRES_URL || '';

let pool = null;
if (DATABASE_URL) {
  try {
    let connStr = DATABASE_URL;
    if (connStr.startsWith('postgres://')) {
      connStr = connStr.replace('postgres://', 'postgresql://');
    }
    const isLocal = connStr.includes('localhost') || connStr.includes('127.0.0.1');
    pool = new Pool({
      connectionString: connStr,
      ssl: isLocal ? false : { rejectUnauthorized: false }
    });
  } catch (e) {
    pool = null;
  }
}

// ----------------- تهيئة التخزين الدائم في PostgreSQL -----------------
async function getPostgresAuthState(sessionKey) {
  if (pool) {
    try {
      await pool.query(`
        CREATE TABLE IF NOT EXISTS baileys_auth_sessions (
          key_id VARCHAR(255) PRIMARY KEY,
          data TEXT NOT NULL,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
      `);

      const writeData = async (key_id, data) => {
        try {
          const serialized = JSON.stringify(data, BufferJSON.replacer);
          await pool.query(`
            INSERT INTO baileys_auth_sessions (key_id, data, updated_at) 
            VALUES ($1, $2, NOW())
            ON CONFLICT (key_id) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW();
          `, [`${sessionKey}_${key_id}`, serialized]);
        } catch (e) {}
      };

      const readData = async (key_id) => {
        try {
          const res = await pool.query('SELECT data FROM baileys_auth_sessions WHERE key_id = $1;', [`${sessionKey}_${key_id}`]);
          if (res.rows.length > 0) return JSON.parse(res.rows[0].data, BufferJSON.reviver);
        } catch (e) {}
        return null;
      };

      const removeData = async (key_id) => {
        try {
          await pool.query('DELETE FROM baileys_auth_sessions WHERE key_id = $1;', [`${sessionKey}_${key_id}`]);
        } catch (e) {}
      };

      let creds = await readData('creds');
      if (!creds) {
        creds = initAuthCreds();
        await writeData('creds', creds);
      }

      return {
        state: {
          creds,
          keys: {
            get: async (type, ids) => {
              const data = {};
              for (const id of ids) {
                let val = await readData(`${type}-${id}`);
                if (type === 'app-state-sync-key' && val) val = proto.Message.AppStateSyncKeyData.fromObject(val);
                data[id] = val;
              }
              return data;
            },
            set: async (data) => {
              for (const category in data) {
                for (const id in data[category]) {
                  const val = data[category][id];
                  const key = `${category}-${id}`;
                  if (val) {
                    await writeData(key, val);
                  } else {
                    await removeData(key);
                  }
                }
              }
            }
          }
        },
        saveCreds: () => writeData('creds', creds)
      };
    } catch (e) {
      console.error(`[PostgresAuth Error - ${sessionKey}]:`, e.message);
    }
  }

  // في حال غياب الاتصال بقاعدة البيانات يتم الاعتماد على مجلد محلي كنسخة احتياطية
  const localFolder = path.join(__dirname, `auth_${sessionKey}`);
  if (!fs.existsSync(localFolder)) fs.mkdirSync(localFolder, { recursive: true });
  return await useMultiFileAuthState(localFolder);
}

// ----------------- كائنات الجلسات -----------------
const sessions = {
  operations: { sock: null, qr: null, connected: false, user: null, isStarting: false },
  sales: { sock: null, qr: null, connected: false, user: null, isStarting: false }
};

// ----------------- الجلسة الأولى: رقم العمليات -----------------
async function startOperationsWhatsApp() {
  if (sessions.operations.isStarting) return;
  sessions.operations.isStarting = true;

  try {
    const { state, saveCreds } = await getPostgresAuthState('operations');

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
          if (pool) {
            try { await pool.query("DELETE FROM baileys_auth_sessions WHERE key_id LIKE 'operations_%';"); } catch (e) {}
          }
          setTimeout(startOperationsWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.operations.connected = true;
        sessions.operations.qr = null;
        sessions.operations.user = sock?.user?.id ? sock.user.id.split(':')[0] : 'متصل';
        sessions.operations.isStarting = false;
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        const msg = m.messages[0];
        if (!msg || !msg.message) return;

        const chatId = msg.key.remoteJid;
        const fromMe = Boolean(msg.key.fromMe);
        const myPhoneNumber = sessions.operations.user ? sessions.operations.user.replace(/[^0-9]/g, '') : '';
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

// ----------------- الجلسة الثانية: رقم مبيعات العملاء الجدد -----------------
async function startSalesWhatsApp() {
  if (sessions.sales.isStarting) return;
  sessions.sales.isStarting = true;

  try {
    const { state, saveCreds } = await getPostgresAuthState('sales');

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
          if (pool) {
            try { await pool.query("DELETE FROM baileys_auth_sessions WHERE key_id LIKE 'sales_%';"); } catch (e) {}
          }
          setTimeout(startSalesWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        sessions.sales.connected = true;
        sessions.sales.qr = null;
        sessions.sales.user = sock?.user?.id ? sock.user.id.split(':')[0] : 'متصل';
        sessions.sales.isStarting = false;
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        const msg = m.messages[0];
        if (!msg || !msg.message || msg.key.fromMe) return;

        const chatId = msg.key.remoteJid;
        if (chatId.endsWith('@g.us')) return;

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
    if (pool) {
      try { await pool.query("DELETE FROM baileys_auth_sessions WHERE key_id LIKE 'operations_%';"); } catch (e) {}
    }
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
    if (pool) {
      try { await pool.query("DELETE FROM baileys_auth_sessions WHERE key_id LIKE 'sales_%';"); } catch (e) {}
    }
    setTimeout(startSalesWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

// بدء تشغيل الجلستين
startOperationsWhatsApp();
startSalesWhatsApp();

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Multi-Session Server] شغال ويستمع على 127.0.0.1:${PORT}`);
});
