# C-ECM Centralized Enterprise Content Management

[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61DAFB)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6)](https://typescriptlang.org)
[![Tests](https://img.shields.io/badge/Tests-322%20passing-brightgreen)]()
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## The Problem: Your Release Process is Bleeding Hours You Can't Get Back

Picture a typical Friday afternoon production incident. Something broke in the last deployment. The on-call engineer needs to find the approved runbook, the change-approval record, the test evidence, and the audit log of who touched the config file — and needs them *now*.

Here's what actually happens:

- The "official" document is in **FileNet** — but which version? Nobody knows which one was signed off.
- The working copy the team actually used is on a **shared drive** — also in three different folders with three different names.
- The change approval lives in an **email thread** from six months ago that nobody has bookmarked.
- The mainframe-attached DMS the infrastructure team uses hasn't been integrated with anything, ever, because nobody wants to touch it.
- The audit tracker is a **spreadsheet** updated manually, sometimes.

That's five systems that don't talk to each other, and the clock is ticking.

**This is not an edge case.** It is the default state for every organization running enterprise-grade workloads — IBM i shops, IBM Z shops, banks, insurers, government agencies — any place where the document estate spans legacy and modern systems simultaneously. And it generates a measurable, recurring tax:

| Pain Point | Real Cost |
|---|---|
| **Incident reconstruction** | 2–4 hours per incident reconstructing who approved what, across which system, at what version |
| **Approval archaeology** | Days of work per compliance audit to prove change-approval trails that should be instant lookups |
| **Version confusion** | Runbooks and deployment checklists exist in 3–5 near-duplicate copies; nobody can tell which is authoritative |
| **Approval-by-email** | Sign-offs that exist only in an inbox — unfindable 6 months later when an auditor asks |
| **Migration paralysis** | Teams want unified governance but cannot afford to move 10 years of content out of FileNet or off a mainframe |

**C-ECM removes this tax — without a migration.**

---

## The Solution: One Governance Layer Over Every System You Already Have

C-ECM is a governance and intelligence layer that sits *in front of* whatever storage each team already uses — IBM FileNet, IBM i (AS/400), IBM Z mainframe, Alfresco, SharePoint, Google Drive, Box, S3, Azure Blob, IBM Cloud Object Storage, local disk — and gives every one of those systems the same approval gates, the same immutable audit trail, and the same cross-repository search, on day one, with zero files moved and zero migrations required.

It looks like a shared drive. It feels like a shared drive. But every action behind the scenes is now:

- **Approval-gated** — documents flow through defined, multi-step, quorum-based sign-off workflows before they can be published or finalized
- **Version-controlled** — every upload creates an immutable version; check-out/check-in prevents silent overwrites with a server-enforced soft lock
- **Audit-logged** — every login, view, edit, approval, rejection, and deletion is captured in a tamper-evident log with user, timestamp, and action type
- **Cross-system-searchable** — one search bar queries every connected backend in parallel and returns ranked results in the time it takes to query the slowest single system

### Who This Is For

| Role | What They Gain |
|---|---|
| **Release / Change Managers** | CAB-style sign-off process with full version history, no email approvals, and a one-click audit export |
| **Platform & DevOps Engineers** | Authoritative, locked runbooks and deployment checklists — one document, one version, one truth |
| **IT Compliance & Audit Teams** | Reconstruct any change-approval trail in under 5 minutes, not 5 days |
| **CIOs & Enterprise Architects** | Zero-migration governance across every storage system in the portfolio, including legacy mainframe systems no SaaS tool touches |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│              C-ECM UI  (React 19 + TypeScript 5)             │
│  Landing · Login · Drive · Document Viewer · Audit Reports   │
└────────────────────────────┬─────────────────────────────────┘
                              │  REST / JSON  (HTTPS)
┌────────────────────────────▼─────────────────────────────────┐
│              FastAPI  ·  Python 3.12  ·  Pydantic v2         │
│                                                               │
│  Routers: files · folders · auth · workflows · activity      │
│           ai · esignature · sharing · users · admin          │
└──────┬──────────────────────┬──────────────────────┬─────────┘
       │                      │                      │
  Domain Services       Event Bus              Repository Layer
  ─────────────         ─────────              ────────────────
  ai_service            activity_service       *_store.py
  notification_service  (audit + notify        (one per domain:
  esignature_service     + webhooks)            documents, tags,
  webhook_service                               workflows, locks,
                                                retention, …)
       │
┌──────▼───────────────────────────────────────────────────────┐
│          Provider Abstraction Layer  (Strategy / Adapter)    │
│              StorageProvider  ABC  (base.py)                 │
└──┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──┘
   │      │      │      │      │      │      │      │      │
FileNet  IBM-i  IBM-Z  Alf  GDrive  One  Box   S3  Azure  Local
                            resco  Drive         Blob/COS  Disk
```

### Event Bus: the secret to a unified audit trail

Every write to any backend — upload, rename, delete, approval vote, version restore — flows through one choke point: `activity_service.record_event()`. That single call fans out synchronously to:

1. **The audit log** (SQLite, append-only, WAL mode)
2. **In-app notifications** (per-user notification store)
3. **Outbound webhooks** (HMAC-SHA256 signed, SSRF-hardened)

No provider has to know the audit log exists. No new provider needs new audit code. The observer/event-bus pattern is what makes a unified, tamper-resistant trail possible across 11 unrelated storage systems.

### Design Patterns in Play

| Pattern | Where | Why |
|---|---|---|
| **Strategy / Adapter** | `StorageProvider` ABC + 11 concrete backends | IBM Z and Google Drive expose identical interfaces to the rest of the app |
| **Observer / Event Bus** | `activity_service` | One `record_event()` call reaches audit, notifications, and webhooks; adding a subscriber never touches provider code |
| **Repository** | `*_store.py` per domain | Routers never write raw SQL; data access is encapsulated and testable |
| **State Machine** | Approval workflow engine | Instances move through validated transitions only (`pending → in_review → approved / rejected / cancelled`) with per-step quorum |
| **Factory / Registry** | `get_provider(key)` in `registry.py` | Self-registers every backend; verified thread-safe under concurrent first access |
| **Layered Architecture** | Presentation → API → Domain → Data → Providers | A UI change or storage-backend swap never leaks across layers |

---

## Feature Set

| Feature | What It Does |
|---|---|
| **11 Storage Backends, One Governance Layer** | FileNet, IBM i (AS/400), IBM Z (mainframe), Alfresco, Google Drive, OneDrive/SharePoint, Box, AWS S3, Azure Blob, IBM Cloud Object Storage, Local Disk — zero migration |
| **Multi-Step Approval Workflows** | Quorum-based sign-off with full vote history and inline action from the document viewer — the same gate a CAB or change-management process needs |
| **Workflow Designer** | Admin UI to build step-based approval definitions; reviewers picked from real accounts, not free-text names |
| **Immutable Audit Trail & Compliance Dashboard** | 360° admin dashboard — date/user/event-category filters, trend and breakdown charts, most-active-users ranking, automatic alerts on failed-login or bulk-delete bursts, one-click CSV export |
| **Cross-Backend Global Search** | Every configured backend queried in parallel; latency bounded by the slowest single connection (not summed); errors isolated per-connection so one bad backend never hides results |
| **Unified Document Viewer** | Full-screen view per file — properties, approvals, AI insights, tags, comments, version history, share links, and actions, each independently collapsible and lazily loaded |
| **Version History & Check-Out/In** | Full version tracking with restore and a server-enforced soft lock (HTTP 423) — two engineers cannot silently overwrite the same runbook |
| **Retention Policies & Legal Hold** | Scheduled retention rules that follow a document across its lifecycle — the record survives even if the file is moved or renamed |
| **Multi-User RBAC** | Admin / Editor / Viewer roles with JWT auth, bcrypt passwords, and server-side session logout — all audited |
| **Outbound Webhooks** | HMAC-SHA256 signed, SSRF-hardened event delivery — external systems (ticketing, SIEM) hear about a change the moment it happens |
| **AI Document Intelligence** | Summarize, classify, and answer natural-language questions about any document — choose Anthropic (Claude), OpenAI/ChatGPT (or any OpenAI-compatible endpoint), local Ollama, IBM watsonx.ai, Watson NLU, or Watson Discovery from **Settings → AI Provider**, no restart needed |
| **E-Signature** | Route documents through DocuSign for a formal, legally binding sign-off — tracked alongside every other event on that document |
| **PWA / Mobile-Responsive** | Installable on a phone; usable during an on-call incident away from a desk |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 19, TypeScript 5, Vite |
| **Backend** | FastAPI, Python 3.12, Pydantic v2 |
| **Authentication** | JWT HS256 + bcrypt, server-side session logout |
| **Database** | Configurable via `FD_DB_ENGINE`: SQLite (default, WAL mode, one file per domain — zero config) or PostgreSQL/Oracle via SQLAlchemy, for a real multi-user production deployment. Schema/indexes are created automatically on first connect either way — see `backend/app/db.py` |
| **Scheduler** | APScheduler (background thread — retention policy enforcement) |
| **AI** | Anthropic (Claude) · OpenAI/ChatGPT (or compatible) · local Ollama · IBM watsonx.ai · Watson NLU · Watson Discovery — swap between them from Settings → AI Provider |
| **E-Signature** | DocuSign (JWT Grant flow) |
| **Testing** | pytest + httpx — 322 integration tests |

---

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 20+

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
cp .env.example .env            # fill in your values
python run.py                   # serves on 0.0.0.0:8020
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5174
```

### Default Login
```
Username: admin
Password: admin
```
> **Change the default password immediately** in Settings → Users.

---

## Production Deployment

The dev setup above is two bare processes and a SQLite file — fine for evaluating the app, not for a real deployment. `deploy/` has four ready-to-use paths, all built on the same Docker images:

```bash
cd deploy
cp .env.production.example .env    # fill in FD_DB_PASSWORD, FD_APP_PASSWORD, FD_OAUTH_REDIRECT_BASE
docker compose up -d --build       # Postgres + backend + nginx, one command
```

| Path | What it does |
|---|---|
| **Standalone** | `docker compose up -d --build` — runs anywhere Docker does (laptop, bare server, on-prem box) |
| **AWS** | `deploy/aws/deploy.sh` — provisions an EC2 instance and starts the same stack on it |
| **Azure** | `deploy/azure/deploy.sh` — provisions an Azure VM and starts the same stack on it |
| **Windows-native** | `deploy/windows/` — only needed for real FileNet content-**write** support, which needs a local WebSphere Java runtime and can't be containerized; runs the backend natively as a Windows Service alongside containerized Postgres/nginx |

Schema/indexes are created automatically against Postgres (or Oracle) on first connect — no separate migration step. See [`deploy/README.md`](deploy/README.md) for full setup instructions, including what's deliberately left out (TLS termination, automated backups, secrets-manager integration) and how to add each.

---

## AI Provider Integration

C-ECM's document intelligence (summarize, classify, Q&A, AI Agents) can run on any of six backends, switchable from **Settings → AI Provider** with no restart:

| Capability | What it does |
|---|---|
| **Summarization** | Extract key points from any document without opening it |
| **Classification** | Automatically assign document classes and route to the right approval workflow |
| **Q&A** | Ask natural-language questions about document content |
| **Workflow suggestion** | Auto-detect the right approval workflow for a document based on its content |

Enter credentials for as many backends as you like from the UI — switching the active one later never loses the others' settings. Or configure via `.env`:

```bash
FD_AI_BACKEND=anthropic             # anthropic | openai | ollama | watsonx | watson_nlu | watson_disco | none
FD_ANTHROPIC_API_KEY=sk-ant-...
FD_ANTHROPIC_MODEL=claude-sonnet-5
```

---

## Security

See [SECURITY.MD](SECURITY.MD) for full credential management guidelines.

**Key points:**
- All credentials in `.env` — never committed (`.gitignore` already configured)
- JWT signing key auto-generated on first run if not set
- Every login, logout, and content view recorded in the audit log
- CORS scoped to loopback + private LAN ranges only
- Outbound webhooks SSRF-hardened (resolved-IP validation, no redirect-following)

---

## Project Structure

```
filenet-drive/
├── backend/
│   ├── app/
│   │   ├── routers/             # FastAPI route handlers (files, auth, workflows, ai, …)
│   │   ├── storage_providers/   # 11 backend adapters + thread-safe provider registry
│   │   ├── *_store.py           # Repository modules (one per domain)
│   │   ├── db.py                # Configurable engine (SQLite/Postgres/Oracle) every store builds on
│   │   ├── activity_service.py  # Event bus → audit + notifications + webhooks
│   │   ├── auth.py              # JWT + RBAC
│   │   ├── main.py              # App entry point
│   │   └── schemas.py           # Pydantic models
│   ├── tests/                   # 322 integration tests
│   └── requirements.txt
├── deploy/                      # Production deployment — standalone/AWS/Azure/Windows-native (see deploy/README.md)
├── frontend/
│   ├── src/
│   │   ├── components/          # React components (Viewer, Workflows, Audit, AI, …)
│   │   ├── pages/               # Landing, Login, Drive, DocumentViewer, AuditLog, Integrations
│   │   ├── contexts/            # Auth context
│   │   ├── icons/               # Inline SVG icon system
│   │   └── types.ts             # TypeScript type definitions
│   └── package.json
├── .env.example                 # Environment variable template
├── .gitignore                   # Prevents credential commits
└── SECURITY.MD                  # Security guidelines
```

---

## Running Tests

```bash
cd backend
.venv\Scripts\python.exe -m pytest tests/ -q
# Expected: 322 passed
# (2 pre-existing failures possible only in sandboxes with no outbound DNS — unrelated to app logic)
```

---

## Team

| Name | Role |
|---|---|
| Mohammad Jamil Ahmed | Lead Developer & Architect |

---

## License

MIT License — see LICENSE file for details.
