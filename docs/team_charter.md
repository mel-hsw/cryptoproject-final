# Team Charter

**Project:** Real-Time Crypto AI Service  
**Date:** November 25, 2025  
**Team Size:** 5 members

---

## Team Overview

This document outlines the roles, responsibilities, and collaboration guidelines for our team building a real-time cryptocurrency volatility detection service.

---

## Team Roles & Responsibilities

### 1. Team Lead / Project Manager
**Responsibilities:**
- Coordinate team activities and ensure deliverables are on track
- Facilitate team meetings and resolve blockers
- Interface with stakeholders and manage scope
- Ensure documentation is complete and up-to-date
- Final review of all deliverables before submission

**Key Deliverables:**
- Weekly status updates
- Risk management and mitigation
- Demo coordination

---

### 2. ML Engineer / Data Scientist
**Responsibilities:**
- Maintain and improve the volatility prediction model
- Monitor model performance and handle retraining
- Implement data drift detection with Evidently
- Optimize model inference latency
- Manage MLflow experiment tracking

**Key Deliverables:**
- Model artifacts (`models/artifacts/`)
- Drift reports (`reports/evidently/`)
- Model card documentation
- Performance benchmarks

---

### 3. Backend / API Developer
**Responsibilities:**
- Develop and maintain FastAPI endpoints
- Implement API contract (`/predict`, `/health`, `/version`, `/metrics`)
- Handle error handling, rate limiting, and request validation
- Ensure API reliability and graceful degradation
- Write integration tests

**Key Deliverables:**
- API service (`api/app.py`)
- API tests (`tests/test_api_integration.py`)
- Load testing results
- API documentation

---

### 4. Infrastructure / DevOps Engineer
**Responsibilities:**
- Manage Docker Compose configuration
- Set up and maintain Kafka (KRaft mode)
- Configure Prometheus and Grafana monitoring
- Implement CI/CD pipeline with GitHub Actions
- Handle system resilience (reconnect, retry, graceful shutdown)

**Key Deliverables:**
- Docker configuration (`docker/`)
- CI/CD pipeline (`.github/workflows/`)
- Grafana dashboards
- Infrastructure runbook

---

### 5. Data Engineer / Streaming Specialist
**Responsibilities:**
- Manage data ingestion from Coinbase WebSocket
- Implement Kafka producers/consumers
- Handle feature engineering pipeline
- Ensure data quality and replay capability
- Manage raw data storage and processing

**Key Deliverables:**
- Ingestion scripts (`scripts/ws_ingest.py`)
- Feature pipeline (`features/featurizer.py`)
- Replay functionality (`scripts/replay_to_kafka.py`)
- Data documentation

---

## Communication Guidelines

### Meetings
- **Daily Standup:** 15 minutes, async via Slack/Discord
- **Weekly Sync:** 1 hour, video call to review progress
- **Ad-hoc:** As needed for blockers or technical discussions

### Communication Channels
- **Primary:** Slack/Discord for daily communication
- **Code Reviews:** GitHub Pull Requests
- **Documentation:** GitHub Wiki or Markdown files in `docs/`
- **Issue Tracking:** GitHub Issues

### Response Times
- Urgent issues: Within 2 hours during working hours
- Normal issues: Within 24 hours
- Code reviews: Within 48 hours

---

## Decision Making

### Technical Decisions
- Minor decisions: Individual team member autonomy
- Significant decisions: Discuss in team sync, consensus required
- Architecture changes: Document in ADR (Architecture Decision Record)

### Conflict Resolution
1. Direct discussion between involved parties
2. Escalate to Team Lead if unresolved
3. Team vote for major disagreements

---

## Code Standards

### Git Workflow
- **Main Branch:** Protected, requires PR approval
- **Feature Branches:** `feature/<name>` or `fix/<name>`
- **Commits:** Conventional commits format
- **Reviews:** At least 1 approval required

### Code Quality
- **Linting:** Black + Ruff (enforced via CI)
- **Testing:** Minimum one integration test per feature
- **Documentation:** Docstrings for all public functions

---

## Weekly Deliverables Timeline

### Week 4 - System Setup & API Thin Slice
| Role | Deliverable |
|------|-------------|
| Team Lead | Team charter, coordinate diagram creation |
| ML Engineer | Model selection, inference validation |
| Backend Dev | `/health`, `/predict`, `/version`, `/metrics` endpoints |
| DevOps | Docker Compose, Kafka KRaft setup |
| Data Engineer | Replay 10-minute dataset, data validation |

### Week 5 - CI, Testing & Resilience
| Role | Deliverable |
|------|-------------|
| Team Lead | README update, coordinate testing |
| ML Engineer | Model performance baseline |
| Backend Dev | Integration tests, load testing |
| DevOps | CI pipeline, .env configuration |
| Data Engineer | Kafka resilience (reconnect, retry) |

### Week 6 - Monitoring, SLOs & Drift
| Role | Deliverable |
|------|-------------|
| Team Lead | SLO documentation, runbook review |
| ML Engineer | Evidently drift detection, rollback mechanism |
| Backend Dev | Prometheus metrics integration |
| DevOps | Grafana dashboards, alerting |
| Data Engineer | Consumer lag monitoring |

### Week 7 - Demo, Handoff & Reflection
| Role | Deliverable |
|------|-------------|
| Team Lead | Demo video, final documentation |
| ML Engineer | Performance summary vs baseline |
| Backend Dev | Failure recovery demonstration |
| DevOps | Final release tagging |
| Data Engineer | Data handoff documentation |

---

## Success Metrics

### Technical
- [ ] One-command startup (`docker compose up -d`)
- [ ] API responds within p95 ≤ 800ms (aspirational)
- [ ] CI pipeline passes (lint + integration test)
- [ ] Model PR-AUC > baseline

### Documentation
- [ ] README ≤10-line setup guide
- [ ] Architecture diagram complete
- [ ] Runbook with troubleshooting steps
- [ ] All required docs in `docs/` folder

### Demo
- [ ] 8-minute demo video
- [ ] Shows startup, prediction, failure recovery, rollback
- [ ] Clean, professional presentation

---

## Appendix: Contact Information

| Role | Name | GitHub | Contact |
|------|------|--------|---------|
| Team Lead | [Name] | @github | email@example.com |
| ML Engineer | [Name] | @github | email@example.com |
| Backend Dev | [Name] | @github | email@example.com |
| DevOps | [Name] | @github | email@example.com |
| Data Engineer | [Name] | @github | email@example.com |

---

*This charter is a living document and may be updated as the project evolves.*

