/**
 * whatsapp_service.js - Multi-Session WhatsApp Engine with Auto-Session Recovery & History Backfill
 * Food Development Company (شركة تنمية الغذاء)
 * Handles auto media decryption, message cache storage, staff whitelisting,
 * edited/revoked message detection, group location buffers, and chat history sync for reconciliation.
 */

const express = require('express');
const { 
  default: makeWASocket, 
  DisconnectReason, 
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
  downloadMediaMessage
} = require('@whiskeysockets/baileys');
const QRCode = require('qrcode');
const axios = require('axios');
const pino = require('pino');
const fs = require('fs');
const path = require('path');

process.on('uncaughtException', (err) => console.error('[Baileys UncaughtException]:', err.message));
process.on('unhandledRejection', (reason) => console.error('[Baileys UnhandledRejection]:', reason));

const app = express();
app.use(express.json({ limit: '60mb' }));

const PORT = 3001;

// كل الاستدعاءات إلى FastAPI تحمل التوكن الداخلي السري (يرثه هذا الـ process من main.py)
// حتى لا تُقبل مسارات الجلسة والـ webhook من خارج الخادم
const coreApi = axios.create({
  baseURL: 'http://127.0.0.1:8000',
  headers: { 'X-Internal-Token': process.env.INTERNAL_API_TOKEN || '' }
});
if (!process.env.INTERNAL_API_TOKEN) {
  console.error('[Baileys] INTERNAL_API_TOKEN missing - calls to the CRM core will be rejected.');
}

function snapshotFolder(folderPath) {
  const snapshot = {};
  if (!fs.existsSync(folderPath)) return snapshot;
  const files = fs.readdirSync(folderPath);
  for (const file of files) {
    const fullPath = path.join(folderPath, file);
    try {
      if (fs.statSync(fullPath).isFile()) snapshot[file] = fs.readFileSync(fullPath, 'utf8');
    } catch (e) {}
  }
  return snapshot;
}

function restoreFolder(folderPath, snapshot) {
  if (!fs.existsSync(folderPath)) fs.mkdirSync(folderPath, { recursive: true });
  for (const [file, content] of Object.entries(snapshot)) {
    try { fs.writeFileSync(path.join(folderPath, file), content, 'utf8'); } catch (e) {}
  }
}

async function restoreSessionFromDB(sessionName, folderPath) {
  try {
    const res = await coreApi.get(`/api/internal/session-snapshot/${sessionName}`, { timeout: 4000 });
    if (res.data && res.data.snapshot && Object.keys(res.data.snapshot).length > 0) {
      restoreFolder(folderPath, res.data.snapshot);
      console.log(`[DB Restore] Successfully restored '${sessionName}' from PostgreSQL.`);
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
        await coreApi.post('/api/internal/session-snapshot', {
          session_name: sessionName,
          snapshot: snapshot
        }, { timeout: 6000 });
      }
    } catch (e) {}
  }, 2000);
}

const sessions = {
  // status: connecting | qr | connected | paused | replaced
  operations: { sock: null, qr: null, connected: false, user: null, isStarting: false, status: 'connecting' }
};

// عدد دورات انتهاء رمز QR دون مسح قبل الإيقاف المؤقت (كل دورة ≈ 2.7 دقيقة)
const MAX_QR_CYCLES = parseInt(process.env.WA_MAX_QR_CYCLES || '3', 10);
let qrTimeoutCycles = 0;
let reconnectAttempts = 0;
let consecutiveDisconnects = 0;
const messageStore = new Map();
const recentGroupLocations = new Map();

function isInternalStaffName(pushName) {
  if (!pushName) return false;
  const upper = pushName.toUpperCase();
  return upper.includes('FDC') || upper.includes('SALES TEAM') || upper.includes('تنمية الغذاء');
}

// معرّفات الرسائل التي أرسلها البوت نفسه، حتى لا تُعامل كرسائل من فريق العمل
const botSentIds = new Set();
function rememberBotMessage(sent) {
  if (sent && sent.key && sent.key.id) {
    botSentIds.add(sent.key.id);
    if (botSentIds.size > 2000) botSentIds.delete(botSentIds.values().next().value);
    if (sent.message) messageStore.set(sent.key.id, sent.message);
  }
}

// تحويل الرقم/المعرف إلى JID: معرفات المجموعات أطول من أرقام الهواتف (أكثر من 15 رقماً) أو تحتوي "-"
function toJid(target) {
  const clean = String(target || '').trim().replace(/[^0-9@a-z._-]/gi, '');
  if (clean.includes('@')) return clean;
  const digits = clean.replace(/^\+/, '');
  return (digits.includes('-') || digits.replace(/\D/g, '').length > 15) ? `${digits}@g.us` : `${digits}@s.whatsapp.net`;
}

const MAX_MEDIA_BYTES = 8 * 1024 * 1024;
const ORDER_DOC_TYPES = ['application/pdf', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                         'text/csv', 'text/plain', 'image/jpeg', 'image/png', 'image/webp'];
const MAX_VOICE_SECONDS = 300;

// تنزيل صورة أو ملف الطلب (جدول الفروع والأصناف) لقراءته بالذكاء الاصطناعي
async function extractOrderMedia(sock, msg, mMsg) {
  const image = mMsg?.imageMessage;
  const doc = mMsg?.documentMessage || mMsg?.documentWithCaptionMessage?.message?.documentMessage;
  const audio = mMsg?.audioMessage;  // الرسائل الصوتية تُفرَّغ إلى نص في الخادم
  const media = image || doc || audio;
  if (!media) return null;
  if (audio && Number(audio.seconds || 0) > MAX_VOICE_SECONDS) return null;
  const mimetype = (media.mimetype || (image ? 'image/jpeg' : audio ? 'audio/ogg' : '')).split(';')[0];
  if (doc && !ORDER_DOC_TYPES.includes(mimetype)) return null;
  if (Number(media.fileLength || 0) > MAX_MEDIA_BYTES) return null;
  try {
    const buffer = await downloadMediaMessage(msg, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage });
    if (!buffer || buffer.length > MAX_MEDIA_BYTES) return null;
    return { media_base64: buffer.toString('base64'), media_mimetype: mimetype, media_filename: doc?.fileName || null };
  } catch (e) {
    console.error('[Operations WA] Media download failed:', e.message);
    return null;
  }
}

function extractQuoted(mMsg) {
  const ctx = mMsg?.extendedTextMessage?.contextInfo || mMsg?.imageMessage?.contextInfo ||
              mMsg?.documentMessage?.contextInfo || mMsg?.videoMessage?.contextInfo || mMsg?.audioMessage?.contextInfo;
  if (!ctx || !ctx.stanzaId) return { quoted_message_id: null, quoted_text: null };
  const q = ctx.quotedMessage;
  let qText = extractMessageText(q);
  if (!qText && q?.imageMessage) qText = '[image]';
  if (!qText && q?.documentMessage) qText = `[file] ${q.documentMessage.fileName || ''}`.trim();
  return { quoted_message_id: ctx.stanzaId, quoted_text: qText || null };
}

// تنفيذ الرسائل التي يطلبها المحرك (سؤال العميل، تنبيه المندوب، أمر التوريد للوجستيك)
async function runActions(sock, actions) {
  for (const a of actions || []) {
    if (!a || !a.to || !a.text) continue;
    try {
      const sent = await sock.sendMessage(toJid(a.to), { text: a.text });
      rememberBotMessage(sent);
    } catch (e) {
      console.error('[Operations WA] Action send failed:', e.message);
    }
  }
}

function extractMessageText(mMsg) {
  if (!mMsg) return '';
  return mMsg.conversation || 
         mMsg.extendedTextMessage?.text || 
         mMsg.imageMessage?.caption || 
         mMsg.documentMessage?.caption || 
         '';
}

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
          sessions.operations.status = 'qr';
        } catch (err) {}
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error)?.output?.statusCode;
        const isRegistered = Boolean(state?.creds?.registered);
        consecutiveDisconnects++;
        sessions.operations.connected = false;
        sessions.operations.isStarting = false;

        console.log(`[Operations WA] Closed. Code: ${statusCode}. Registered: ${isRegistered}. Consecutive: ${consecutiveDisconnects}`);

        // 1) تسجيل خروج من الهاتف أو جلسة تالفة: مسح الجلسة وتوليد QR جديد
        if (statusCode === DisconnectReason.loggedOut || statusCode === DisconnectReason.badSession) {
          console.log('[Operations WA] Logged out / bad session. Clearing credentials for a fresh QR.');
          try {
            fs.rmSync(authFolder, { recursive: true, force: true });
            await coreApi.delete('/api/internal/session-snapshot/operations_main');
          } catch (e) {}
          consecutiveDisconnects = 0;
          qrTimeoutCycles = 0;
          sessions.operations.status = 'connecting';
          setTimeout(startOperationsWhatsApp, 2000);
          return;
        }

        // 2) فُتحت الجلسة من جهاز/خادم آخر: لا نعيد الاتصال حتى لا يطرد كل منهما الآخر
        if (statusCode === DisconnectReason.connectionReplaced) {
          console.log('[Operations WA] Session replaced by another client. Paused until requested from dashboard.');
          sessions.operations.status = 'replaced';
          sessions.operations.qr = null;
          return;
        }

        // 3) مرحلة انتظار المسح: انتهى رمز QR دون أن يُمسح
        if (!isRegistered) {
          qrTimeoutCycles++;
          if (qrTimeoutCycles >= MAX_QR_CYCLES) {
            console.log(`[Operations WA] QR not scanned after ${qrTimeoutCycles} cycles. Paused - request a new QR from the dashboard.`);
            sessions.operations.status = 'paused';
            sessions.operations.qr = null;
            return;
          }
          setTimeout(startOperationsWhatsApp, 3000);
          return;
        }

        // 4) جلسة صالحة انقطعت مؤقتاً (شبكة/خادم واتساب): إعادة اتصال بتأخير متزايد دون مسح الجلسة
        reconnectAttempts++;
        const delay = statusCode === DisconnectReason.restartRequired ? 1000 : Math.min(60000, 3000 * Math.pow(2, reconnectAttempts - 1));
        sessions.operations.status = 'connecting';
        console.log(`[Operations WA] Reconnecting in ${Math.round(delay / 1000)}s (attempt ${reconnectAttempts}).`);
        setTimeout(startOperationsWhatsApp, delay);
      } else if (connection === 'open') {
        consecutiveDisconnects = 0;
        reconnectAttempts = 0;
        qrTimeoutCycles = 0;
        sessions.operations.status = 'connected';
        sessions.operations.connected = true;
        sessions.operations.qr = null;
        const cleanPhone = sock?.user?.id ? sock.user.id.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
        sessions.operations.user = cleanPhone;
        sessions.operations.isStarting = false;
        console.log(`[Operations WA] Active & Persistent on: ${cleanPhone}`);
        debouncedSaveSessionToDB('operations_main', authFolder);
      }
    });

    sock.ev.on('messages.upsert', async (m) => {
      if (!m.messages || m.messages.length === 0) return;
      // معالجة كل الرسائل في الدفعة (كان يُعالج الأول فقط فتضيع طلبيات تصل معاً)
      for (const msg of m.messages) {
        try {
          await processIncomingMessage(sock, msg);
        } catch (e) {
          console.error('[Operations WA] Message processing error:', e.message);
        }
      }
    });

  } catch (err) {
    console.error('[Operations WA] Startup error:', err.message);
    sessions.operations.isStarting = false;
    setTimeout(startOperationsWhatsApp, 5000);
  }
}

async function processIncomingMessage(sock, msg) {
      {

        if (msg.key && msg.key.id && msg.message) {
          messageStore.set(msg.key.id, msg.message);
          if (messageStore.size > 1000) {
            const firstKey = messageStore.keys().next().value;
            messageStore.delete(firstKey);
          }
        }

        if (!msg.message) return;

        const chatId = msg.key.remoteJid;
        const isFromMe = Boolean(msg.key.fromMe);
        // رسائلنا من رقم الشركة: نهتم فقط بالمكتوبة يدوياً (في المجموعات كسياق، وفي المحادثات الخاصة
        // حتى يعرف البوت أن أحداً من الفريق يتولى المحادثة فيسكت)، لا برسائل البوت نفسه
        if (isFromMe && botSentIds.has(msg.key.id)) return;
        if (isFromMe && (chatId.includes('@newsletter') || chatId.includes('@broadcast'))) return;

        let rawSender = isFromMe ? ((sock.user && sock.user.id) || '').split(':')[0] : (msg.key.participant || chatId);
        rawSender = rawSender.split('@')[0];
        let senderPhone = rawSender.replace(/[^0-9]/g, '');

        if (senderPhone.startsWith('968') && senderPhone.length > 11) {
          senderPhone = senderPhone.slice(0, 11);
        } else if (senderPhone.length > 12) {
          const match968 = senderPhone.match(/968\d{8}/);
          if (match968) senderPhone = match968[0];
        }

        const senderName = msg.pushName || senderPhone;

        // رسائل فريق العمل تُرسل كسياق للمحرك (مثل سؤال المندوب عن الطلب) ولا تُعامل كطلبات
        const isStaff = isFromMe || isInternalStaffName(senderName);

        const isRevoked = msg.message?.protocolMessage?.type === 0;
        if (isRevoked && isStaff) return;
        if (isRevoked) {
          const targetMsgId = msg.message.protocolMessage.key?.id;
          const delResp = await coreApi.post('/api/whatsapp/webhook', {
            chat_id: chatId,
            sender_phone: `+${senderPhone}`,
            sender_name: senderName,
            message_text: '',
            message_id: targetMsgId,
            event_type: 'MESSAGE_DELETED'
          }, { timeout: 8000 });
          // إرسال تنبيه إلغاء الطلبية لمجموعة اللوجستيك (كان الرد يُتجاهل فلا يصل التنبيه)
          if (delResp.data && delResp.data.forward_to_logistics && delResp.data.logistics_text) {
            const logJid = toJid(delResp.data.forward_to_logistics);  // مجموعة أو رقم هاتف
            rememberBotMessage(await sock.sendMessage(logJid, { text: delResp.data.logistics_text }));
          }
          await runActions(sock, delResp.data && delResp.data.actions);
          return;
        }

        const isEdited = Boolean(msg.message?.protocolMessage?.editedMessage);
        let mMsg = isEdited ? msg.message.protocolMessage.editedMessage : (
          msg.message?.ephemeralMessage?.message || 
          msg.message?.viewOnceMessage?.message || 
          msg.message
        );

        let locationUrl = '';
        const locMsg = mMsg?.locationMessage || mMsg?.liveLocationMessage;
        if (locMsg && locMsg.degreesLatitude && locMsg.degreesLongitude) {
          locationUrl = `https://maps.google.com/?q=${locMsg.degreesLatitude},${locMsg.degreesLongitude}`;
          recentGroupLocations.set(chatId, { url: locationUrl, timestamp: Date.now() });
        }

        let text = extractMessageText(mMsg);

        if (recentGroupLocations.has(chatId)) {
          const locData = recentGroupLocations.get(chatId);
          if (Date.now() - locData.timestamp < 5 * 60 * 1000) {
            locationUrl = locData.url;
          } else {
            recentGroupLocations.delete(chatId);
          }
        }

        if (mMsg?.stickerMessage || mMsg?.reactionMessage) return;

        // الصور والملفات (جداول الطلبات) تُقرأ فقط من العملاء داخل المجموعات
        const media = (!isStaff && chatId.endsWith('@g.us') && !isEdited) ? await extractOrderMedia(sock, msg, mMsg) : null;
        const quoted = extractQuoted(mMsg);

        if (!text.trim() && !locationUrl && !media) return;

        const originalMsgId = isEdited ? msg.message.protocolMessage.key?.id : msg.key.id;

        const resp = await coreApi.post('/api/whatsapp/webhook', {
          chat_id: chatId,
          sender_phone: `+${senderPhone}`,
          sender_name: senderName,
          message_text: text,
          message_id: originalMsgId,
          event_type: isEdited ? 'MESSAGE_EDITED' : 'NORMAL',
          shared_location_url: locationUrl,
          is_from_me: isFromMe,
          sender_is_staff_hint: isStaff,
          quoted_message_id: quoted.quoted_message_id,
          quoted_text: quoted.quoted_text,
          ...(media || {})
        }, { timeout: media ? 90000 : 40000 });

        await runActions(sock, resp.data && resp.data.actions);

        if (resp.data && resp.data.reply_text) {
          const sent = await sock.sendMessage(chatId, { text: resp.data.reply_text });
          rememberBotMessage(sent);
        }

        if (resp.data && resp.data.send_catalog && resp.data.catalog_path && fs.existsSync(resp.data.catalog_path)) {
          const fileBuffer = fs.readFileSync(resp.data.catalog_path);
          const sentDoc = await sock.sendMessage(chatId, {
            document: fileBuffer,
            mimetype: 'application/pdf',
            fileName: 'Food_Development_Catalog.pdf',
            caption: 'كتالوج منتجات شركة تنمية الغذاء'
          });
          rememberBotMessage(sentDoc);
        }

        if (resp.data && resp.data.forward_to_logistics && resp.data.logistics_text) {
          const logJid = toJid(resp.data.forward_to_logistics);  // مجموعة أو رقم هاتف
          rememberBotMessage(await sock.sendMessage(logJid, { text: resp.data.logistics_text }));
        }
      }
}

app.get('/qr-status', (req, res) => {
  res.json({
    connected: sessions.operations.connected,
    user: sessions.operations.user,
    qr: sessions.operations.qr,
    status: sessions.operations.status
  });
});

// طلب رمز QR جديد / استئناف الاتصال بعد الإيقاف المؤقت (من زر في اللوحة)
app.post('/start', (req, res) => {
  if (sessions.operations.connected) return res.json({ status: 'ALREADY_CONNECTED' });
  qrTimeoutCycles = 0;
  reconnectAttempts = 0;
  sessions.operations.status = 'connecting';
  sessions.operations.isStarting = false;
  startOperationsWhatsApp();
  return res.json({ status: 'STARTING' });
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

app.post('/fetch-group-messages', async (req, res) => {
  const { group_jid, limit = 100 } = req.body;
  if (!sessions.operations.connected || !sessions.operations.sock) {
    return res.status(503).json({ error: 'خدمة الواتساب غير متصلة', messages: [] });
  }

  try {
    const fetchedMessages = [];
    for (const [msgId, rawMsg] of messageStore.entries()) {
      const mMsg = rawMsg?.ephemeralMessage?.message || 
                   rawMsg?.viewOnceMessage?.message || 
                   rawMsg;
      const text = extractMessageText(mMsg);
      if (text) {
        fetchedMessages.push({
          message_id: msgId,
          text: text,
          timestamp: Date.now()
        });
      }
    }
    return res.json({ status: 'SUCCESS', messages: fetchedMessages.slice(-limit) });
  } catch (e) {
    return res.status(500).json({ error: e.message, messages: [] });
  }
});

app.post('/send-message', async (req, res) => {
  const { phone_or_group, message } = req.body;
  if (!sessions.operations.connected || !sessions.operations.sock) {
    return res.status(503).json({ error: 'خدمة الواتساب غير متصلة' });
  }

  try {
    const jid = toJid(phone_or_group);

    try { await sessions.operations.sock.presenceSubscribe(jid); } catch (e) {}

    const sent = await sessions.operations.sock.sendMessage(jid, { text: message });
    rememberBotMessage(sent);
    return res.json({ status: 'SENT', to: jid });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

app.post('/send-document', async (req, res) => {
  const { phone_or_group, caption, file_base64, file_name } = req.body;
  if (!sessions.operations.connected || !sessions.operations.sock) {
    return res.status(503).json({ error: 'خدمة الواتساب غير متصلة' });
  }

  try {
    const jid = toJid(phone_or_group);
    const docBuffer = Buffer.from(file_base64, 'base64');
    const fname = (file_name || 'attachment.pdf').toLowerCase();

    try { await sessions.operations.sock.presenceSubscribe(jid); } catch (e) {}

    let sent;
    if (fname.endsWith('.png') || fname.endsWith('.jpg') || fname.endsWith('.jpeg') || fname.endsWith('.webp')) {
      const mime = fname.endsWith('.png') ? 'image/png' : 'image/jpeg';
      sent = await sessions.operations.sock.sendMessage(jid, {
        image: docBuffer,
        mimetype: mime,
        caption: caption || ''
      });
    } else {
      sent = await sessions.operations.sock.sendMessage(jid, {
        document: docBuffer,
        mimetype: 'application/pdf',
        fileName: file_name || 'Document.pdf',
        caption: caption || ''
      });
    }

    if (sent && sent.key && sent.message) {
      messageStore.set(sent.key.id, sent.message);
    }

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
    consecutiveDisconnects = 0;
    qrTimeoutCycles = 0;
    reconnectAttempts = 0;
    sessions.operations.status = 'connecting';
    if (sessions.operations.sock) {
      try { await sessions.operations.sock.logout(); } catch (e) {}
      try { sessions.operations.sock.end(); } catch (e) {}
    }
    const authFolder = path.join(__dirname, 'auth_info');
    try { fs.rmSync(authFolder, { recursive: true, force: true }); } catch (e) {}
    try { await coreApi.delete('/api/internal/session-snapshot/operations_main'); } catch (e) {}
    setTimeout(startOperationsWhatsApp, 1500);
    return res.json({ status: 'DISCONNECTED' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
});

setTimeout(startOperationsWhatsApp, 2500);

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Baileys Server] Active on port ${PORT}`);
});
