-- Cleaned and enriched version of Bronze. Types are converted, skills are extracted,
-- and work type is detected from the description. Bronze rows are never deleted or modified.
CREATE TABLE IF NOT EXISTS silver_job_postings (
    job_id              TEXT PRIMARY KEY,
    title               TEXT,
    description         TEXT,
    location            TEXT,
    company             TEXT,
    salary_min          FLOAT,
    salary_max          FLOAT,
    salary_is_predicted BOOLEAN,           -- Converted from "0"/"1" string in Bronze.
    created             DATE,              -- Converted from ISO string in Bronze.
    category            TEXT,
    country             TEXT,
    work_type           TEXT,              -- "remote", "hybrid", "on-site", or NULL if not mentioned.
    skills              TEXT[],            -- PostgreSQL array of skill names extracted from title + description.
    skills_source       TEXT,              -- "llm" if extracted by Groq, "regex" if fallback. NULL for pre-tracking rows.
    role_type           TEXT,              -- LLM-classified canonical role (e.g. "data_engineering"). NULL until classified.
    transformed_at      TIMESTAMP          -- When this row was processed by Silver.
);
