const express = require('express');
const { 
  default: makeWASocket, 
  DisconnectReason, 
  BufferJSON, 
  initAuthCreds, 
  proto 
} = require('@whiskeysockets/baileys');
const QRCode = require('qrcode');
const axios = require('axios');
const pino = require('pino');
const { Pool } = require('pg');

const app = express();
app.use(express.json());

const PORT = 3001;
const DATABASE_URL = process.env.DATABASE_URL || process.env.DATABASE_PUBLIC_URL || process.env.POSTGRES_URL || '';

let pool = null;
if (DATABASE_URL) {
  let connStr = DATABASE_URL;
  if (connStr.startsWith('postgres://')) {
    connStr = connStr.replace('postgres://', 'postgresql://');
  }
  pool = new Pool({ connectionString: connStr, ssl: { rejectUnauthorized: false } });
}

async function initDbAuth() {
  if (!pool) return;
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS baileys_auth_sessions (
        key_id VARCHAR(255) PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      );
    `);
    console.log('[Postgres Baileys] جدول جلسات الواتساب الدائم مفعل وجاهز.');
  } catch (e) {
    console.error('[Postgres Baileys Error]', e);
  }
}

// محول حفظ الجلسة داخل PostgreSQL
async function usePostgresAuthState() {
  await initDbAuth();

  const writeData = async (key_id, data) => {
    if (!pool) return;
    try {
      const serialized = JSON.stringify(data, BufferJSON.replacer);
      await pool.query(`
        INSERT INTO baileys_auth_sessions (key_id, data, updated_at) 
        VALUES ($1, $2, NOW())
        ON CONFLICT (key_id) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW();
      `, [key_id, serialized]);
    } catch (e) {
      console.error('[Postgres Write Error]', e);
    }
  };

  const readData = async (key_id) => {
    if (!pool) return null;
    try {
      const res = await pool.query('SELECT data FROM baileys_auth_sessions WHERE key_id = $1;', [key_id]);
      if (res.rows.length > 0) {
        return JSON.parse(res.rows[0].data, BufferJSON.reviver);
      }
    } catch (e) {
      console.error('[Postgres Read Error]', e);
    }
    return null;
  };

  const removeData = async (key_id) => {
    if (!pool) return;
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
}

let latestQR = null;
let isConnected = false;
let connectedUser = null;
let sock = null;

async function startWhatsApp() {
  console.log('[Baileys] تهيئة محرك جلسة الواتساب المتصل بقاعدة البيانات...');
  try {
    const { state, saveCreds } = await usePostgresAuthState();

    if (sock) {
      try { sock.ev.removeAllListeners(); } catch (e) {}
    }

    sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['FDC Sales CRM', 'Chrome', '10.0.0']
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        latestQR = await QRCode.toDataURL(qr);
        isConnected = false;
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
        isConnected = false;
        latestQR = null;
        console.log(`[Baileys Connection Closed] كود: ${statusCode}، إعادة الاتصال: ${shouldReconnect}`);
        
        if (shouldReconnect) {
          setTimeout(startWhatsApp, 3000);
        }
      } else if (connection === 'open') {
        console.log('✅ [Baileys] متصل بنجاح ومثبت في قاعدة البيانات الدائمة!');
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
          sender_phone: `+${senderPhone.replace(/^\+/, '')}`,
          sender_name: senderName,
          message_text: text
        }, { timeout: 4000 });
      } catch (e) {}
    });

  } catch (err) {
    console.error('[Baileys Error]:', err);
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
    return res.status(503).json({ error: 'جلسة الواتساب غير متصلة' });
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
      await pool.query('DELETE FROM baileys_auth_sessions;');
    }

    setTimeout(startWhatsApp, 2000);
    return res.json({ status: 'DISCONNECTED', message: 'تم إنهاء الجلسة ومسحها من قاعدة البيانات' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

startWhatsApp();

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Server] Listening on 127.0.0.1:${PORT}`);
});
