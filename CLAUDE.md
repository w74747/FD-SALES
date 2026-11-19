# FD-Sales — CLAUDE.md

## What this is
B2B bakery (FDC, Oman) sales & order-operations system. WhatsApp bot takes customer
orders and routes them: customer → data entry → finance approval → logistics.
Plus product catalogue, customer pricing, and management reports.

## Stack (actual)
- Python FastAPI backend (`main.py` is the app + most routes). Modules:
  - `order_engine.py` — smart order parsing / one-order-per-customer-per-date logic
  - `order_approval.py` — data entry → finance → logistics step
  - `ai_client.py` — LLM calls with provider fallback, in `AI_PROVIDERS` order (default
    `deepseek,anthropic,together`): DeepSeek `deepseek-flash` (cheapest; text + images + tools,
    OpenAI-compatible), then Claude (Messages API), then Together (Qwen). Callers pass
    Anthropic-style content blocks; converted per provider. PDFs only go to Claude. No sampling
    params (Claude 5.5 models 400 on them); Claude Sonnet/Opus 5.5 reject forced tool_choice →
    auto + instruction + retry. `POST /api/system/ai-check` tests every configured key.
  - `media_tools.py` — Word/Excel/CSV/PDF text extraction; voice via Whisper on Together
  - `catalog.py` — Assortment Excel import, packing specs, unit conversion (pieces / kg)
  - `branch_import.py` — bulk branch import: Excel template, preview, apply (no auto-delete),
    branch alternate names used by the bot's branch matching (DB-free, unit-tested)
  - `knowledge.py` — knowledge base for bot auto-replies: product catalog first, then active
    docs, up to `KB_MAX_CHARS` (default 200k chars, keeps Haiku 5.5 under its 100K-token price
    tier). `GET /api/knowledge/status` reports what was cut — never cut silently.
  - `direct_chat.py` — auto-reply to anyone messaging the company number directly (not a
    customer group): KB-only answers, never prices/orders, welcome once per 30 days, hold message
    + admin alert when unknown, silent 30 min after a manual staff reply, max 8 bot replies/hour.
    Setting `direct_auto_reply_enabled` (off by default). Internal numbers (admin, logistics,
    data entry) and sales reps are never auto-answered.
  - `reports.py`, `sales_reports_engine.py` — management reports (PDF/Excel + WhatsApp summary)
  - `i18n.py` — all bot-facing text
  - `security.py` — HMAC-signed HttpOnly session cookie, /api/* guard, internal token, 2FA rate limit
  - `db_pool.py` — PostgreSQL connection pool
  - `logo_data.py` + `assets/` — FDC logo; `fonts/` — report fonts
- `dashboard.html`: single-page admin dashboard
- `whatsapp_service.js`: Node + Baileys WhatsApp service. It is started by `main.py`
  as a subprocess (`node whatsapp_service.js`); internal routes require `INTERNAL_API_TOKEN`.
  Manual messages typed from the company number (fromMe, not bot-sent) are forwarded to the
  webhook — in groups as context, in direct chats so the bot goes quiet.
- PostgreSQL (Railway), connection pool
- LLM for order parsing/KB answers: DeepSeek first, Claude/Together fallback; Whisper Large v3 via Together for voice notes
- Auth: TOTP 2FA → server-signed cookie

## Environment variables (names only — never commit values)
- Auth / security: `ADMIN_SETUP_TOKEN`, `APP_SECRET_KEY`, `INTERNAL_API_TOKEN`,
  `SESSION_TTL_HOURS`, `COOKIE_SECURE`, `ALLOWED_ORIGINS`
- Database: `DATABASE_URL` (fallbacks: `DATABASE_PUBLIC_URL`, `POSTGRES_URL`),
  `DB_MAX_CONNECTIONS`, `DB_ACQUIRE_TIMEOUT`
- AI: `AI_PROVIDERS`, `DEEPSEEK_API_KEY`, `DEEPSEEK_TEXT_MODEL`, `DEEPSEEK_VISION_MODEL`,
  `DEEPSEEK_BASE_URL`, `TOGETHER_TEXT_MODEL`, `TOGETHER_VISION_MODEL`, `TOGETHER_BASE_URL`,
  `ANTHROPIC_API_KEY`, `ANTHROPIC_TEXT_MODEL`, `ANTHROPIC_VISION_MODEL`,
  `TOGETHER_API_KEY`, `TRANSCRIBE_MODEL`, `PERPLEXITY_API_KEY` (optional, used in main.py)
- Knowledge base: `KB_MAX_CHARS` (default 200000), `KB_MAX_DOC_CHARS` (default 150000)
- App: `PORT`, `PUBLIC_BASE_URL`, `RAILWAY_PUBLIC_DOMAIN`, `BUSINESS_UTC_OFFSET`,
  `ORDER_AUTO_CONFIRM_THRESHOLD`
- WhatsApp service: `WA_MAX_QR_CYCLES`
- Secrets live in Railway variables / a local `.env` (git-ignored).

## Deploy
- Railway: service FD-SALES + Postgres. `railway.json` builds from `Dockerfile`
  (node:20 image + Python 3; `CMD python3 main.py`), healthcheck `/healthz`
  (timeout 120s), restart ON_FAILURE (max 5).
- `Procfile` (uvicorn) exists but is not used by the Dockerfile build.
- Git-ignored runtime dirs: `auth_info/`, `uploads/`.

## Tests
- Tests live in `test_*.py` at the repo root; run with `python -m pytest -q`.
  Currently: `test_branch_import.py`, `test_ai_and_knowledge.py`, `test_direct_chat.py`.
  The "199/199 tests" reported in the claude.ai sessions were never committed.
- pytest is not in requirements.txt (dev-only); install it in the venv.
- Local dev on this Windows ARM64 machine: `.venv` (git-ignored via .git/info/exclude).
  `psycopg2-binary` has no win-arm64 wheel, so modules importing psycopg2 (`main.py`,
  `order_engine.py`, `db_pool.py`) can't be imported locally — keep business logic in
  DB-free modules so it can be tested.

## Workflow
- Branch from main → PR → merge → Railway auto-deploys → check deploy logs.
- All bot-facing text lives in i18n.py (English default, Arabic per party).
- Never translate product, branch or unit names.
- Write Arabic comments for sales-process business logic.

## Core rules
- One order per customer per delivery date (add / modify / cancel).
- Order confirmation: ask customer + approval link to rep or system admin.
- Data entry gets FD-<n> ref, replies with system order no. (e.g. BKLSO-202602343;
  the "BKLSO - 202602343" screen format is also accepted),
  then "approved/معتمد" after finance → logistics gets APPROVED order.
  Out-of-order approval via quoted message. "Rejected/مرفوض" alerts rep/admin.
  Cancel after logistics → notify logistics immediately.
- Customer-specific prices; price snapshot stored at order time; price history kept.
- Units converted to pieces / kg.

## Original product spec (not yet implemented — confirm before building)
- Pipeline: Discovery → Sample Dispatch → Feedback Loop → (Modify | Approve) → First Order Won
- RBAC + device binding: reps bound to one device; Sales Director can reset devices and
  use multiple devices; Team Leaders oversee reps and approve changes.
- Quality loop: Instant Approval / Needs Modification / High Rejection → Production/R&D reports.
- Excel import/export of products (SKU, Name, Description, Weight).
- Spec originally named Refine + Supabase + FingerprintJS + SheetJS + Recharts;
  current code is FastAPI + HTML. Ask before switching stacks.

## Known open issues
- Test coverage is minimal (see Tests).
- Claude has never worked in production: every call in the Railway logs up to 2026-11-19
  returned 401 invalid x-api-key. Since 2026-11-20 DeepSeek is tried first, so Claude is only a
  fallback; DeepSeek/Together keys were never exercised before — verify with the dashboard test. Even with a valid key,
  the old code would have hit 400s (temperature / forced tool_choice) — fixed 2026-11-20.
- Real Claude/Whisper calls still unverified end-to-end in production.
- Products file: 29 items missing packing specs; conflict on SKU 0911;
  duplicate "Potato Bun 4pcs" (0122 / 0142).
- Historical orders still to be imported.

## Backlog
- Role permissions + device binding
- WhatsApp Cloud API for campaigns
- Rep visit logging with location
- Overdue-order reminder to data entry
- Capture actual order value from data entry for reports

## Project notes (Obsidian)
- Project notes live in the Obsidian vault:
  - `C:\Users\abaom\OneDrive\Desktop\Abdullah's Brain\Projects\FD-Sales.md`
  - `C:\Users\abaom\OneDrive\Desktop\Abdullah's Brain\Projects\FD-Sales - Claude Code Handoff.md`
- Update `FD-Sales.md` **in Arabic** at the end of every work session
  (status, a dated log entry, decisions, next steps).
