/**
 * whatsapp_service.js - Enterprise WhatsApp Bot & Dispatch Engine
 * Supports Self-Messaging Bot, Multi-User Group Listening, and Logistics Forwarding
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

async function initPostgresStorage() {
  if (!pool) return false;
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS baileys_auth_sessions (
        key_id VARCHAR(255) PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      );
    `);
    return true;
  } catch (e) {
    return false;
  }
}

async function getHybridAuthState() {
  const pgReady = await initPostgresStorage();

  if (pgReady && pool) {
    const writeData = async (key_id, data) => {
      try {
        const serialized = JSON.stringify(data, BufferJSON.replacer);
        await pool.query(`
          INSERT INTO baileys_auth_sessions (key_id, data, updated_at) 
          VALUES ($1, $2, NOW())
          ON CONFLICT (key_id) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW();
        `, [key_id, serialized]);
      } catch (e) {}
    };

    const readData = async (key_id) => {
      try {
        const res = await pool.query('SELECT data FROM baileys_auth_sessions WHERE key_id = $1;', [key_id]);
        if (res.rows.length > 0) {
          return JSON.parse(res.rows[0].data, BufferJSON.reviver);
        }
      } catch (e) {}
      return null;
    };

    const removeData = async (key_id) => {
      try {
        await pool.query('DELETE FROM baileys_auth_sessions WHERE key_id = $1;', [key_id]);
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
              let value = await readData(`${type}-${id}`);
              if (type === 'app-state-sync-key' && value) {
                value = proto.Message.AppStateSyncKeyData.fromObject(value);
              }
              data[id] = value;
            }
            return data;
          },
          set: async (data) => {
            for (const category in data) {
              for (const id in data[category]) {
                const value = data[category][id];
                const key = `${category}-${id}`;
                if (value) {
                  await writeData(key, value);
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
  } else {
    const authFolder = path.join(__dirname, 'auth_info');
    if (!fs.existsSync(authFolder)) {
      fs.mkdirSync(authFolder, { recursive: true });
    }
    return await useMultiFileAuthState(authFolder);
  }
}

let latestQR = null;
let isConnected = false;
let connectedUser = null;
let myJid = null;
let sock = null;
let isStarting = false;

async function startWhatsApp() {
  if (isStarting) return;
  isStarting = true;

  try {
    const { state, saveCreds } = await getHybridAuthState();

    if (sock) {
      try { sock.ev.removeAllListeners(); } catch (e) {}
    }

    sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['FDC Sales CRM', 'Chrome', '11.0.0']
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        try {
          latestQR = await QRCode.toDataURL(qr);
          isConnected = false;
        } catch (err) {}
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
        isConnected = false;
        latestQR = null;

        if (shouldReconnect) {
          isStarting = false;
          setTimeout(startWhatsApp, 3000);
        } else {
          isStarting = false;
          if (pool) {
            try { await pool.query('DELETE FROM baileys_auth_sessions;'); } catch (e) {}
          }
          setTimeout(startWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        isConnected = true;
        latestQR = null;
        myJid = sock?.user?.id ? sock.user.id.split(':')[0] + '@s.whatsapp.net' : null;
        connectedUser = sock?.user?.id ? sock.user.id.split(':')[0] : 'متصل';
        isStarting = false;
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      try {
        const msg = m.messages[0];
        if (!msg || !msg.message) return;

        const chatId = msg.key.remoteJid;
        const fromMe = Boolean(msg.key.fromMe);
        const myPhoneNumber = connectedUser ? connectedUser.replace(/[^0-9]/g, '') : '';
        const isSelfChat = chatId.includes(myPhoneNumber);

        // تجاهل الرسائل الصادرة باستثناء الرسائل الموجهة لنفسك (Self-Messaging)
        if (fromMe && !isSelfChat) return;

        const senderPhone = (msg.key.participant || chatId).split('@')[0];
        const senderName = msg.pushName || senderPhone;
        const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';

        if (!text) return;

        // إرسال الرسالة إلى الباكيند لمعالجتها والرد عليها
        const resp = await axios.post('http://127.0.0.1:8000/api/whatsapp/webhook', {
          chat_id: chatId,
          sender_phone: `+${senderPhone.replace(/^\+/, '')}`,
          sender_name: senderName,
          message_text: text
        }, { timeout: 6000 });

        // إذا أعاد الباكيند رداً تلقائياً (مثل الرد على استفسارك في الشات الخاص أو تأكيد طلب)
        if (resp.data && resp.data.reply_text) {
          await sock.sendMessage(chatId, { text: resp.data.reply_text });
        }

        // إذا تطلب الأمر إعادة التوجيه التلقائي لفريق اللوجستيك
        if (resp.data && resp.data.forward_to_logistics && resp.data.logistics_text) {
          const logJid = resp.data.forward_to_logistics.includes('@g.us') 
            ? resp.data.forward_to_logistics 
            : `${resp.data.forward_to_logistics.replace(/[^0-9]/g, '')}@s.whatsapp.net`;
          await sock.sendMessage(logJid, { text: resp.data.logistics_text });
        }

      } catch (e) {}
    });

  } catch (err) {
    isStarting = false;
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

app.get('/groups', async (req, res) => {
  if (!isConnected || !sock) return res.json([]);
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

app.post('/send-message', async (req, res) => {
  if (!isConnected || !sock) {
    return res.status(503).json({ error: 'خدمة الواتساب غير متصلة حالياً' });
  }

  const { phone_or_group, message } = req.body;
  try {
    let cleanTarget = phone_or_group.replace(/[^0-9@a-z._-]/gi, '');
    let jid = cleanTarget.endsWith('@g.us') ? cleanTarget : `${cleanTarget.replace(/^\+/, '')}@s.whatsapp.net`;

    await sock.sendMessage(jid, { text: message });
    return res.json({ status: 'SENT', to: jid });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

app.post('/disconnect', async (req, res) => {
  try {
    isConnected = false;
    connectedUser = null;
    latestQR = null;

    if (sock) {
      try { await sock.logout(); } catch (e) {}
      try { sock.end(); } catch (e) {}
    }

    if (pool) {
      try { await pool.query('DELETE FROM baileys_auth_sessions;'); } catch (e) {}
    }

    isStarting = false;
    setTimeout(startWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED', message: 'تم فك الارتباط ومسح الجلسة بنجاح' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

startWhatsApp();

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Server] شغال ويستمع على 127.0.0.1:${PORT}`);
});
