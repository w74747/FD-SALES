# تحديث رقم الاختبار الموحد وتعديل مسارات الوكلاء في main.py

@app.get("/api/agents")
def get_ai_agents():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents ORDER BY id ASC;")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/agents/{agent_id}/update")
def update_ai_agent(agent_id: int, payload: dict):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE ai_agents 
            SET name = %s, system_prompt = %s, trigger_schedule = %s 
            WHERE id = %s;
            """, (payload.get("name"), payload.get("system_prompt"), payload.get("trigger_schedule"), agent_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/agents/test-global")
async def test_agent_global(payload: dict):
    agent_id = payload.get("agent_id")
    test_target = payload.get("test_phone", "").strip()

    if not test_target:
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف الاختبار الموحد")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="قاعدة البيانات غير متصلة")

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents WHERE id = %s;", (agent_id,))
            agent = cur.fetchone()
            if not agent:
                raise HTTPException(status_code=404, detail="الوكيل غير موجود")

            cur.execute("SELECT * FROM sample_deliveries ORDER BY id DESC LIMIT 1;")
            sample = cur.fetchone()
            sample_info = f"العميل: {sample['customer_name']}، المنتج: {sample['product_name']}" if sample else "العميل: مطاعم الريف، المنتج: صدور دجاج 4B"

            if agent["role_type"] == "SAMPLES_CONVERSION":
                message_text = (
                    f"🤖 *متابعة تجريبية من: {agent['name']}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"أهلاً بك، يرجى موافاتنا بنتيجة تجربة العينات الميدانية لدى:\n"
                    f"📍 {sample_info}\n\n"
                    f"عند الاعتماد نرجو تسجيل رقم أمر الشراء (PO) في النظام."
                )
            else:
                message_text = (
                    f"🤖 *رسالة تجريبية من: {agent['name']}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"التوجيه النشط:\n«{agent['system_prompt']}»\n\n"
                    f"شركة تنمية الغذاء | FDC Sales CRM"
                )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:3001/send-message",
                json={"phone_or_group": test_target, "message": message_text},
                timeout=6.0
            )
            if resp.status_code == 200:
                return {"status": "SUCCESS", "to": test_target, "message_preview": message_text}
            else:
                err = resp.json()
                raise HTTPException(status_code=400, detail=err.get("error", "فشل الإرسال عبر الواتساب"))
    finally:
        conn.close()
