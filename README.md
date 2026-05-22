# Job Industry Trend Tracker

A live data engineering pipeline that tracks skill demand, salary trends, and job posting volume across IT, Finance, and Engineering industries in the US and Canada.

**Live dashboard: https://job-trend-tracker.streamlit.app**

---

## What It Does
A scheduled daily pipeline pulls up to 300 job postings from the Adzuna API across six country/industry combinations and stores them in a PostgreSQL database (Supabase). Over time, the accumulated data answers questions that no single API call can answer:

- Which skills are growing in demand across IT, Finance, and Engineering?
- How do salary trends differ between the US and Canada?
- Are data engineering skills crossing traditional industry boundaries?

---

## Architecture

```
Adzuna API > Bronze (raw) > Silver (cleaned) > Gold (aggregated) > Streamlit Dashboard
```

**Bronze/Silver/Gold medallion architecture:**
- **Bronze** — Raw API responses, never modified. Source of truth.
- **Silver** — Cleaned and normalized. Types converted, skills extracted via LLM (Groq/Llama 3.1) with regex fallback, work type detected here.
- **Gold** — Daily snapshots of skill frequency, salary trends, and posting volume by country and by category.

**Infrastructure:**
- Pipeline runs on GitHub Actions (daily cron + manual dispatch), writes to Supabase (hosted PostgreSQL on AWS).
- Dashboard deployed on Streamlit Community Cloud, reads from Supabase
- Historical data accumulates from April 2026 forward

---

## Tech Stack

| Layer | Tools |
|---|---|
| Ingestion | Python, Requests, Adzuna API |
| Storage | PostgreSQL (Supabase) |
| Transformation | Python, psycopg2, Groq API (Llama 3.1), Regex fallback |
| Orchestration | GitHub Actions |
| Dashboard | Streamlit, Plotly, Pandas |
| Deployment | Streamlit Community Cloud, Supabase |

---

## Key Design Decisions

- **LLM skill extraction with regex fallback** — Groq (Llama 3.1) extracts skills from job descriptions without a hardcoded list, catching new tools and frameworks as they emerge. Regex runs as fallback if the API is unavailable, so the pipeline never fails silently.
- **No backfilling**, tracking forward from April 2026. Pre-AI job market data would introduce noise from conditions that no longer apply.
- **Gold idempotency**, each daily Gold run deletes and re-inserts today's rows, so running the pipeline twice in a day always reflects the freshest data.
- **Skill frequency is a lower bound**, Adzuna truncates descriptions at ~500 characters. Counts undercount BUT trends are reliable because truncation is consistent across all snapshots.

---

## Project Structure

```
├── .github/workflows/pipeline.yml      # GitHub Actions: daily pipeline schedule
├── ingestion/adzuna_ingest.py          # Bronze: pulls from Adzuna API
├── transformation/silver_transform.py  # Silver: cleans and extracts skills
├── gold/gold_transform.py              # Gold: daily aggregation snapshots
├── database/                           # SQL schema files
├── utils/db_connection.py              # Shared PostgreSQL connection
└── dashboard.py                        # Streamlit dashboard
```