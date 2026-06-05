-- Skill mention frequency per role_type and country per snapshot date.
-- This is the role-grained companion to gold_skill_frequency: same counting, but grouped by the
-- LLM-classified role_type (data_engineering, accounting, ...) from Silver instead of Adzuna's
-- broad category. role_type cross-cuts category, so this answers "what does data_engineering
-- demand?" rather than the noisier "what does it-jobs demand?".
--
-- 'other' is intentionally excluded: it is a heterogeneous bucket (project managers, loan officers,
-- and other non-technical roles Adzuna's broad categories sweep in), so its aggregated skills would
-- be meaningless. This is a downside of the Adzuna free tier, handled here rather than pretended away.
--
-- skill_posting_count is a lower bound because Adzuna truncates descriptions at around 500 characters,
-- so skills mentioned after that cutoff are invisible. The trend direction is still reliable because
-- the truncation is consistent across all snapshots.
CREATE TABLE IF NOT EXISTS gold_role_type_skill_frequency (
    snapshot_date       DATE,
    skill               TEXT,
    country             TEXT,
    role_type           TEXT,
    skill_posting_count INT,    -- Number of postings for this role mentioning this skill. Lower bound due to truncation.
    total_postings      INT,    -- Total Silver postings for this country/role_type. Denominator for pct.
    pct_of_postings     FLOAT,  -- skill_posting_count / total_postings * 100.
    aggregated_at       TIMESTAMP,
    PRIMARY KEY (snapshot_date, skill, country, role_type)
);
