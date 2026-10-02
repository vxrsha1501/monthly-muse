# MonthlyMuse

**AI-Powered Automated Message Generator for Monthly Posting** — a web application that prepares
your monthly post before you ask for it. You define a recurring *Monthly Plan* (topic, audience,
platform, tone, posting day); a scheduler detects the upcoming month, gathers occasion/season
context, and runs a hybrid AI pipeline that over-generates candidate messages, then embeds,
scores, de-duplicates and ranks them with our own NLP engine. You receive **three ranked,
stylistically different candidates with transparent score breakdowns**, and every action you take
feeds a personalization engine so next month's output measurably improves.

Built end-to-end from the *MonthlyMuse Master Blueprint* (product & UX Sections 3–4, AI/NLP
pipeline Section 6, math core Section 5, DB schema Section 9, API Section 10, worker Section 8,
frontend Section 11, design system Section 12, evaluation Section 16, golden tests Section 17).

---

## Tech stack (summary)

| Layer | Technology |
|---|---|
| Backend API | Python 3.10+, **FastAPI**, Pydantic v2, Uvicorn |
| Database | **SQLite** by default (zero setup) — schema is PostgreSQL-ready, `MM_DATABASE_URL` swaps it |
| ORM / migrations | SQLAlchemy 2.0 (declarative, 16 tables), Alembic-compatible |
| AI / NLP | Pure **NumPy/SciPy**: own 384-d hashing embedder + preprocessing, cosine similarity, weighted scoring `s = F · w`, gradient weight update, MMR, Wilson/Dirichlet confidence, Thompson sampling, chi-square, entropy |
| LLM generation | Pluggable adapters: **template (default, offline)**, OpenAI, Anthropic, Ollama — with retry + circuit breaker + cost metering |
| Tone classifier | Own softmax logistic regression (800-row labeled set, trained at startup, `.npz` cache) |
| Scheduler | **APScheduler** in-process + standalone worker: daily cycle preparation, T-3/T-1 reminders, catch-up, plan extension, job audit table |
| Auth | Argon2id password hashing, JWT access + refresh (httpOnly cookie), signed ICS calendar tokens |
| Frontend | **Next.js 14 (App Router, TypeScript)**, Tailwind CSS, TanStack Query v5, React Hook Form + Zod, Recharts, lucide-react |
| Tests | **62 passing**: golden-value math tests (Section 17), pipeline tests, full API journey, scheduler automation |

---

## Repository structure

```
monthlymuse/
├── README.md  docker-compose.yml  .github/workflows/ci.yml
├── backend/
│   ├── app/
│   │   ├── api/         # 57 routes: auth, plans, cycles, messages, analytics, calendar, admin
│   │   ├── ai/          # embedder, similarity, features, scoring, mmr, confidence, arms,
│   │   │                # tone, llm_adapters, prompt, templates, limits, pipeline
│   │   ├── core/        # config, errors, security, logging, rate_limit, runtime
│   │   ├── db/          # session (UTCDateTime), models (16 tables)
│   │   ├── schemas/     # Pydantic request/response models
│   │   ├── scheduler/   # jobs.py + runner.py (APScheduler)
│   │   └── services/    # account, catalog, plan, generation, feedback, history, notification, analytics
│   ├── tests/           # 62 tests (golden values, pipeline, API, scheduler)
│   ├── Dockerfile  pyproject.toml
├── frontend/
│   ├── app/             # login, register, dashboard, create, review/[requestId], calendar,
│   │                    # plans, history, analytics, settings
│   ├── components/      # AppShell, Providers, ui primitives (design-system tokens)
│   ├── lib/             # api client (silent refresh), auth provider, shared types
│   ├── tailwind.config.ts  app/globals.css   # Section 12 design tokens
│   └── Dockerfile  package.json
├── data/                # occasions_seed.csv, topics_seed.csv, templates.json, tone_training.csv
├── scripts/             # seed.py (demo data), generate_tone_data.py
├── notebooks/           # 01_cosine_calibration, 02_tone_classifier, 03_ablation_study, 04_simulated_users
└── docs/                # api.md, tech-stack.md (+ PDF)
```

---

## Quick start (local, no Docker required)

Prerequisites: **Python 3.10+**, **Node 18+**.

### 1. Backend

```bash
cd backend
pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --port 8000
```

Seed the demo account first — run it from the **repo root** (it resolves `data/` relative to the repo):

```bash
python scripts/seed.py           # demo user: demo@monthlymuse.app / monthlymuse-demo
```

On startup the app creates tables, seeds catalog data (60 occasions, 20 topics, 40 templates),
trains the tone classifier, and starts the in-process scheduler.

Optional standalone scheduler worker (same jobs, separate process):

```bash
cd backend && python -m app.scheduler.runner
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                      # http://localhost:3000
```

The frontend reads the API base URL from `NEXT_PUBLIC_API_URL` (default
`http://localhost:8000/api/v1`).

### 3. Verify

```bash
cd backend && python -m pytest tests/ -q     # 62 passed
curl http://localhost:8000/api/v1/admin/health
# {"status":"ok","embedder":"hashing-384","tone_labels":8,"llm_provider":"template",...}
```

Log in as `demo@monthlymuse.app` / `monthlymuse-demo`, open the dashboard ("November post ready
for review"), review the three ranked candidates with score rings, compare mode, the embedding
map — then select/edit/reject and watch the personalization update.

---

## Docker Compose (optional)

```bash
docker compose up --build
# API :8000 · Frontend :3000 · PostgreSQL :5432 (MM_DATABASE_URL overridden automatically)
```

Compose sets `MM_DATABASE_URL=postgresql+psycopg://...`, `MM_ENVIRONMENT=production` and runs the
worker as its own service. SQLite remains the zero-setup default outside Compose.

---

## Configuration (env prefix `MM_`, or `backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `MM_DATABASE_URL` | `sqlite:///./monthlymuse.db` | Swap to `postgresql+psycopg://...` — schema unchanged |
| `MM_ENVIRONMENT` | `development` | `development \| test \| production` |
| `MM_CORS_ORIGINS` | `http://localhost:3000,...` | Allowed origins |
| `MM_SECRET_KEY` | dev value | JWT signing key — **change in production** |
| `MM_ACCESS_TOKEN_TTL_MINUTES` / `MM_REFRESH_TOKEN_TTL_DAYS` | 15 / 14 | Token lifetimes |
| `MM_EMBEDDER` | `hash` | `hash` (local, deterministic 384-d) or `sentence-transformers` (optional extra) |
| `MM_LLM_PROVIDER` | `template` | `template \| openai \| anthropic \| ollama` |
| `MM_LLM_MODEL` | `gpt-4o-mini` | Model name for hosted providers |
| `MM_LLM_MONTHLY_BUDGET_USD` | 5.0 | Cost guardrail (PRICING table → usage metrics) |
| `MM_GENERATIONS_PER_HOUR` | 10 | Per-user generation rate limit |
| `MM_SCHEDULER_ENABLED` | `true` | In-process APScheduler |
| `MM_SCHEDULER_TIMEZONE` | `Asia/Kolkata` | Reminder/cycle timezone |
| `MM_EMAIL_BACKEND` | `console` | `console \| smtp \| resend` |

---

## How the AI pipeline works (Section 6)

1. **Understand context** — plan + audience + occasion/season + platform + tone preferences.
2. **Retrieve history** — previous posts, embeddings, selected/rejected feedback.
3. **Generate 6 candidates** — LLM adapter (or offline template engine, ~40 templates + slot banks).
4. **Quality gates** — length limits (platform × language), banned words, emoji level, dedup.
5. **Embed** — own 384-d embedder; cosine similarity against history and each other.
6. **Score** — six features (relevance, tone, personalization, novelty, length, history) →
   `s = F · w` with default weights `.30/.20/.20/.15/.10/.05`.
7. **Diversify** — MMR (`λ=0.7`) with style-arm quotas so the top-3 differ.
8. **Confidence** — Monte-Carlo / Wilson interval per candidate.
9. **Rank → top 3** — full breakdown stored per message for the "why ranked" UI.
10. **Personalize** — selections update tone counts, Beta arms (Thompson sampling), length
    stats, and gradient weight updates (`λ=0.005`, `β=8`, `lr=0.01`).

All Section 17 golden values (scores, gradient step, MMR order, Wilson CI, choice probabilities)
are asserted in `backend/tests/test_math.py`.

---

## Testing

```bash
cd backend
rm -f test_monthlymuse.db
python -m pytest tests/ -q        # 62 passed
```

- `test_math.py` — 35 golden-value tests straight from Section 17.
- `test_pipeline.py` — 12 pipeline/constraint/diversity tests.
- `test_api.py` — full journey: register → plan → generate → poll → feedback → approve → analytics.
- `test_scheduler.py` — reminder days, idempotent preparation, catch-up, plan extension.

CI (`.github/workflows/ci.yml`) runs backend tests and the frontend typecheck/build on every push.
