# Team Charter

**Project:** Real-Time Crypto AI Service  
**Date:** December 5, 2025  
**Team Size:** 2 members
**Team Size:** Asli Gulcur, Melissa wong
---

## Team Overview

This document outlines the roles, responsibilities, and collaboration guidelines for our 2-person team building a real-time cryptocurrency volatility detection service. Due to the small team size, we will adopt a "Full Stack Data" vs. "Full Stack Platform" division of labor.

---

## Team Roles & Responsibilities

### 1. Member A: ML & Data Lead
**Focus:** The "Brain" and "Pipeline."

**Responsibilities:**
- **ML Engineering:** Model selection, training, retraining, and inference optimization.
- **Data Engineering:** Managing WebSocket ingestion, Kafka producers/consumers, and feature engineering.
- **Drift Detection:** Implementing Evidently for data/concept drift monitoring.
- **Model Operations:** Managing MLflow and model artifact versioning.

**Key Deliverables:**
- Feature pipeline (`features/featurizer.py`) & Ingestion scripts
- Model artifacts and performance benchmarks
- Drift reports (`reports/evidently/`)
- Replay functionality logic

### 2. Member B: Platform & DevOps Lead (Project Lead)
**Focus:** The "Skeleton," "API," and "Infrastructure."

**Responsibilities:**
- **Backend API:** Developing FastAPI endpoints, request validation, and error handling.
- **Infrastructure:** Managing Docker Compose (app + Kafka + monitoring stack).
- **DevOps:** Setting up CI/CD (GitHub Actions) and Grafana/Prometheus dashboards.
- **Project Management:** Coordinating submission, documentation, and the final demo video.

**Key Deliverables:**
- API service (`api/app.py`) & Integration tests
- Docker configuration (`docker/`) & CI pipelines
- Grafana dashboards & System runbook
- Final Demo Video editing/production

---

## Communication Guidelines

### Meetings
- **Daily Sync:** Asynchronous via chat. 5-minute call only if blocked.
- **Weekly Deep Dive:** 1 hour (Video) to merge code, review progress, and plan the next sprint.
- **Pair Programming:** Ad-hoc sessions are encouraged for complex integrations (e.g., connecting Kafka to the Model).

### Communication Channels
- **Primary:** Slack/Discord (Continuous contact).
- **Code Reviews:** GitHub Pull Requests (Review required before merge).
- **Documentation:** Markdown files in `docs/` (Keep it lightweight).

---

## Decision Making

- **Consensus:** Since there are only two members, major architectural decisions require agreement from both.
- **Tie-Breaking:** - If it concerns **Data/Model accuracy**, Member A has the final say.
    - If it concerns **System stability/API/Deployment**, Member B has the final say.

---

## Code Standards

- **Branching:** Short-lived feature branches (`feature/<name>`).
- **Main Branch:** Protected. Both members must approve the other's PR to merge to main.
- **Linting:** Black + Ruff (enforced via CI) to prevent style arguments.
- **Testing:** "You build it, you test it." Integration tests are mandatory for the API; Accuracy benchmarks are mandatory for the Model.

