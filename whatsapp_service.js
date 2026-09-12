// إدارة الجلسات المزدوجة
const sessions = {
  operations: { sock: null, qr: null, connected: false, user: null },
  sales: { sock: null, qr: null, connected: false, user: null }
};

// تشغيل جلسة مبيعات العملاء الجدد
async function startSalesWhatsApp() {
  const authFolder = path.join(__dirname, 'auth_sales');
  if (!fs.existsSync(authFolder)) fs.mkdirSync(authFolder, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(authFolder);
  const sock = makeWASocket({
    auth: state,
    logger: pino({ level: 'silent' }),
    printQRInTerminal: false,
    browser: ['FDC Sales Bot', 'Chrome', '1.0.0']
  });

  sessions.sales.sock = sock;
  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, qr } = update;
    if (qr) sessions.sales.qr = await QRCode.toDataURL(qr);
    if (connection === 'open') {
      sessions.sales.connected = true;
      sessions.sales.qr = null;
      sessions.sales.user = sock?.user?.id?.split(':')[0];
    } else if (connection === 'close') {
      sessions.sales.connected = false;
      setTimeout(startSalesWhatsApp, 4000);
    }
  });

  sock.ev.on('messages.upsert', async (m) => {
    const msg = m.messages[0];
    if (!msg || !msg.message || msg.key.fromMe) return;

    const chatId = msg.key.remoteJid;
    if (chatId.endsWith('@g.us')) return; // البوت يتعامل فقط مع المحادثات الفردية للعملاء

    const senderPhone = chatId.split('@')[0];
    const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';
    if (!text) return;

    // توجيه المحادثة للباك إند لمعالجة الذكاء والتوزيع الميداني
    try {
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
}

// مسار فحص حالة جلسة المبيعات
app.get('/sales/qr-status', (req, res) => {
  res.json({
    connected: sessions.sales.connected,
    user: sessions.sales.user,
    qr: sessions.sales.qr
  });
});

startSalesWhatsApp();
