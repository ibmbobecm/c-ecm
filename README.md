# FileDrive — Enterprise Content Management Platform

> **IBM Watsonx Hackathon Project** | Team: FileDrive | Track: Enterprise AI

[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61DAFB)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6)](https://typescriptlang.org)
[![Tests](https://img.shields.io/badge/Tests-67%20passing-brightgreen)]()

---

## 🎯 Problem Statement

Large organisations store documents across **multiple disconnected systems** — IBM FileNet, SharePoint, Google Drive, Amazon S3, Azure Blob, and others. There is no single place to browse, approve, search, or govern them all. Every ECM vendor solves this by locking you into their own storage.

**FileDrive solves it differently**: a provider-agnostic ECM layer that sits on top of any storage backend — without migrating a single file.

---

## 🚀 What FileDrive Does

```
┌──────────────────────────────────────────────────────┐
│              FileDrive UI (React 19 + TypeScript)     │
└───────────────────────┬──────────────────────────────┘
                        │  REST API  (FastAPI · Python 3.12)
┌───────────────────────▼──────────────────────────────┐
│          Provider Abstraction Layer (Strategy)        │
└─┬──────┬──────┬────────┬──────┬───┬───────┬──────────┘
  │      │      │        │      │   │       │
FileNet Alfres Google OneDrive Box  S3  Azure/COS  Local
```

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| **9 Storage Backends** | FileNet, Alfresco, Google Drive, OneDrive/SharePoint, Box, AWS S3, Azure Blob, IBM COS, Local Disk |
| **Multi-User RBAC** | Admin / Editor / Viewer roles with JWT authentication |
| **Document Check-Out / Check-In** | Soft-lock while editing — HTTP 423 enforced server-side |
| **Approval Workflows** | Multi-step, multi-reviewer approvals with full audit trail |
| **Workflow Designer** | Admin UI to create definitions with step builder |
| **Custom Metadata** | Typed document schemas (Invoice, Contract, etc.) attached to any file |
| **Retention Policies** | Scheduled retention checks, legal hold support |
| **Outbound Webhooks** | HMAC-SHA256 signed event delivery to external systems |
| **AI Document Intelligence** | Summarise, classify, Q&A via OpenAI or local Ollama |
| **Cross-Backend Global Search** | All backends queried in parallel — one search, every result |
| **Version History** | Full version tracking with restore |
| **Tags, Comments, Share Links** | Collaborative annotation on any document |
| **PWA / Mobile-Responsive** | Installable, works on phone and tablet |

---

## 🏗️ Architecture & Design Patterns

- **Strategy / Adapter** — `StorageProvider` ABC with 9 concrete backends
- **Observer / Event Bus** — `activity_service.subscribe()` fans out to notifications + webhooks
- **Repository** — each domain has its own store module; routers never write raw SQL
- **State Machine** — workflow instances transition through validated states
- **Factory** — `get_provider(key)` registry; new backends self-register
- **Layered Architecture** — Presentation → API → Domain → Data → Providers

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, TypeScript 5, Vite 6 |
| Backend | FastAPI, Python 3.12, Pydantic v2 |
| Auth | JWT HS256, bcrypt |
| Database | SQLite (WAL, per-domain DB files) |
| Scheduler | APScheduler (background thread) |
| AI | OpenAI API or local Ollama |
| Testing | pytest, httpx — 67 tests, 0 failures |

---

## 🚀 Quick Start

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
cp ../.env.example ../.env      # fill in your values
uvicorn app.main:app --reload --port 8000
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
> Change the default password immediately in Settings → Users.

---

## 🔒 Security

See [SECURITY.MD](SECURITY.MD) for credential management guidelines.

Key points:
- All credentials go in `.env` (never committed)
- `.bobignore` prevents AI assistants from logging credentials
- JWT signing key is auto-generated on first run if not set

---

## 📁 Project Structure

```
filenet-drive/
├── backend/
│   ├── app/
│   │   ├── routers/          # FastAPI route handlers
│   │   ├── storage_providers/ # 9 backend adapters
│   │   ├── *_store.py        # Repository modules
│   │   ├── auth.py           # JWT + RBAC
│   │   ├── main.py           # App entry point
│   │   └── schemas.py        # Pydantic models
│   ├── tests/                # 67 integration tests
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/       # React components
│   │   ├── pages/            # Drive, Login pages
│   │   ├── contexts/         # Auth, Connections context
│   │   ├── icons/            # Inline SVG icon system
│   │   └── types.ts          # TypeScript types
│   └── package.json
├── .env.example              # Environment variable template
├── .gitignore                # IBM Hackathon security gitignore
├── .bobignore                # Bob AI credential protection
└── SECURITY.MD               # Security guidelines
```

---

## 🧪 Running Tests

```bash
cd backend
.venv\Scripts\python.exe -m pytest tests/ -q
# Expected: 67 passed
```

---

## 🤖 IBM Watsonx / AI Integration

FileDrive integrates with IBM Watsonx and OpenAI for document intelligence:

- **Summarisation** — extract key points from any document
- **Classification** — automatically assign document classes
- **Q&A** — ask natural-language questions about document content

Configure via `.env`:
```
FD_AI_PROVIDER=openai       # or "ollama" for local
OPENAI_API_KEY=<your-key>   # stored in .env, never committed
```

---

## 👥 Team

| Name | Role |
|------|------|
| Mohammad Jamil Ahmed | Lead Developer & Architect |

---

## 📄 License

MIT License — see LICENSE file for details.

---

*Built with ❤️ for the IBM Watsonx Hackathon*
