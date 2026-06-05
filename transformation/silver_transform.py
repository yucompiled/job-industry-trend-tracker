import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.db_connection import get_connection
from datetime import datetime
import re

import json
import time
from groq import Groq, RateLimitError

# Skills we scan for in job postings. Keyword matching was chosen over NLP because
# this list is bounded and known, making it transparent and easy to audit over time.
SKILLS = [
    # Languages
    "Python", "SQL", "Java", "Scala", "Go", "JavaScript", "TypeScript",
    "C++", "C#", "Rust", "Bash", "MATLAB", "SAS", "Julia", "VBA", "Perl",

    # Data engineering frameworks and orchestration
    "Spark", "Kafka", "Airflow", "dbt", "Flink", "Hadoop", "Hive",
    "Prefect", "Luigi", "MLflow", "Dagster", "Fivetran", "Airbyte",
    "Polars", "DuckDB", "Trino", "Presto", "Delta Lake", "Apache Iceberg",

    # Cloud platforms
    "AWS", "GCP", "Azure", "Google Cloud",

    # Cloud services
    "S3", "Glue", "Athena", "EMR", "Lambda",
    "Azure Data Factory", "Azure Synapse",
    "Dataflow", "Pub/Sub",

    # Databases and warehouses
    "PostgreSQL", "MySQL", "MongoDB", "Snowflake", "Redshift", "BigQuery",
    "Databricks", "Cassandra", "Redis", "Elasticsearch", "Oracle",
    "DynamoDB", "ClickHouse", "SQL Server", "Teradata", "Vertica", "MariaDB",

    # DevOps and infrastructure
    "Docker", "Kubernetes", "Terraform", "Ansible", "Linux", "Git",
    "Jenkins", "GitHub Actions", "GitLab", "CI/CD", "Helm",

    # BI and visualization
    "Tableau", "Power BI", "Looker", "Grafana", "Excel",
    "Metabase", "Superset", "Alteryx", "Qlik", "SSRS", "SSIS", "Power Query",

    # ML and AI
    "Pandas", "NumPy", "scikit-learn", "TensorFlow", "PyTorch",
    "XGBoost", "LightGBM", "Keras", "spaCy", "Hugging Face", "LangChain",

    # Finance-specific
    "Bloomberg", "QuickBooks", "SAP", "Workday", "Salesforce",
    "Hyperion", "NetSuite",
]

REMOTE_PATTERNS = [r"fully remote", r"100%\s*remote", r"work from home", r"\bwfh\b", r"\bremote\b"]
HYBRID_PATTERNS = [r"\bhybrid\b"]
ONSITE_PATTERNS = [r"\bon.?site\b", r"in.office", r"in the office"]

# Skills that need exact case matching to avoid false positives.
# "Go" is a common English word and would match finance job descriptions without this.
CASE_SENSITIVE_SKILLS = {"Go"}

# Variant spellings (lowercase key) -> canonical display name. Curated from observed LLM output.
# Normalizing here in Silver stops Gold from splitting one real skill across several spellings
# (e.g. SAS / SAS Viya / SAS Studio all counted separately).
SKILL_ALIASES = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "oracle db": "Oracle",
    "oracle database": "Oracle",
    "reactjs": "React",
    "react.js": "React",
    "react js": "React",
    "nodejs": "Node.js",
    "node js": "Node.js",
    "javac": "Java",
    "k8s": "Kubernetes",
    "sas viya": "SAS",
    "sas studio": "SAS",
    "sas enterprise miner": "SAS",
    "sas base": "SAS",
    "powerbi": "Power BI",
    "power-bi": "Power BI",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "amazon web services": "AWS",
    "google cloud platform": "GCP",
    "microsoft excel": "Excel",
    "ms excel": "Excel",
    "apache spark": "Spark",
    "apache kafka": "Kafka",
    "apache airflow": "Airflow",
    "apache hadoop": "Hadoop",
    "apache hive": "Hive",
    "apache flink": "Flink",
}

# Reverse index: canonical (lowercase) -> all spellings to check when grounding, so a skill the
# LLM normalized (e.g. "AWS") is still matched when the posting wrote a variant ("Amazon Web Services").
_SKILL_SPELLINGS = {}
for _variant, _canon in SKILL_ALIASES.items():
    _SKILL_SPELLINGS.setdefault(_canon.lower(), {_canon.lower()}).add(_variant)

# Canonical role types. The LLM must pick exactly one from this list.
# Constrained output prevents the model from inventing taxonomy we can't defend.
ROLE_TYPES = [
    "data_engineering",
    "data_science",
    "ml_engineering",
    "data_analytics",
    "software_engineering",
    "devops",
    "security_engineering",
    "mobile_engineering",
    "financial_analyst",
    "accounting",
    "quantitative_finance",
    "mechanical_engineering",
    "electrical_engineering",
    "civil_engineering",
    "chemical_engineering",
    "other",
]

# Fallback should LLM fail
def extract_skills(description):
    if not description:
        return []
    found = []
    for skill in SKILLS:
        pattern = r"\b" + re.escape(skill) + r"\b"
        # Most skills are case-insensitive, but short ambiguous words like "Go" need exact casing.
        if skill in CASE_SENSITIVE_SKILLS:
            if re.search(pattern, description):
                found.append(skill)
        else:
            if re.search(pattern, description, re.IGNORECASE):
                found.append(skill)
    return found


# --- Skill cleaning: grounding + canonicalization -----------------------------
def canonicalize_skill(skill):
    """Map a single skill to its canonical spelling, or return it unchanged."""
    return SKILL_ALIASES.get(skill.strip().lower(), skill.strip())


def _skill_in_text(skill, text):
    """True if `skill` appears in `text`, not flanked by other alphanumerics.
    The custom boundary handles symbol skills like C++/C#/.NET that \\b mishandles, and
    prevents substring hits (e.g. 'Java' inside 'JavaScript', 'Go' inside 'category')."""
    pattern = r"(?<![A-Za-z0-9])" + re.escape(skill) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def clean_skills(skills, text):
    """Ground, then canonicalize, an extracted skill list.

    1. Grounding  - drop any skill whose spelling does not literally appear in the source text.
                    Kills LLM hallucinations (e.g. 'dbt' on a bass-fishing posting) while keeping
                    the LLM's open vocabulary. Restores the precision of keyword matching.
    2. Canonical  - collapse variant spellings (Postgres -> PostgreSQL, SAS Viya -> SAS).
    3. Dedupe     - one entry per canonical skill, original order preserved.
    """
    if not skills:
        return []
    text = text or ""
    cleaned = []
    seen = set()
    for skill in skills:
        if not isinstance(skill, str) or not skill.strip():
            continue
        canon = canonicalize_skill(skill)
        # Ground against the raw form, the canonical, and any known alias spellings.
        candidates = {skill.lower(), canon.lower()} | _SKILL_SPELLINGS.get(canon.lower(), set())
        if not any(_skill_in_text(c, text) for c in candidates):
            continue
        if canon.lower() not in seen:
            seen.add(canon.lower())
            cleaned.append(canon)
    return cleaned


# --- Groq rate-limit handling -------------------------------------------------
# Free-tier TPM is only 6,000 tokens/min. Under load Groq returns HTTP 429 with a
# short retry-after (often <1s) — those are TRANSIENT: wait the told interval and
# retry the same call. A daily (TPD) exhaustion sends a long retry-after that will
# not clear until the UTC-midnight reset; that is TERMINAL, so we surface it and
# let the caller stop instead of spinning against an empty quota.
TRANSIENT_RETRY_CAP_SECONDS = 60
MAX_RATE_LIMIT_RETRIES = 8


def _retry_after_seconds(error, default=5.0):
    """Best-effort read of how long Groq wants us to wait, in seconds."""
    response = getattr(error, "response", None)
    if response is not None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
    # Header missing (common for sub-second waits) — parse the message text.
    message = str(error)
    ms = re.search(r"try again in ([\d.]+)ms", message)
    if ms:
        return float(ms.group(1)) / 1000.0
    secs = re.search(r"try again in ([\d.]+)s", message)
    if secs:
        return float(secs.group(1))
    return default


def groq_chat_with_retry(client, **create_kwargs):
    """Call chat.completions.create, waiting out transient per-minute 429s.

    Re-raises RateLimitError when the limit is a daily one (retry-after longer
    than a minute) or when retries are exhausted, so the caller decides to stop.
    """
    last_error = None
    for _ in range(MAX_RATE_LIMIT_RETRIES):
        try:
            return client.chat.completions.create(**create_kwargs)
        except RateLimitError as e:
            last_error = e
            wait = _retry_after_seconds(e)
            if wait > TRANSIENT_RETRY_CAP_SECONDS:
                raise  # daily budget — will not clear until UTC midnight
            time.sleep(wait + 0.5)  # small buffer so the rolling window has cleared
    raise last_error


def extract_skills_llm(title, description, client):
    try:
        prompt = f"""Extract only the technical skills, tools, programming languages, and technologies that are EXPLICITLY written in this job posting. Do not infer, guess, or add anything that is not literally present in the text.
Return ONLY a JSON array of skill strings, for example ["...", "..."]. No explanation, no markdown, no extra text.
Prefer short, common names over long vendor forms.
If no skills are explicitly stated, return: []

Job posting:
Title: {title}
Description: {description}"""
        response = groq_chat_with_retry(
            client,
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON array found in response: {raw[:100]}")
        skills = json.loads(match.group())
        if isinstance(skills, list):
            raw = [s for s in skills if isinstance(s, str)]
            return clean_skills(raw, (title or "") + " " + (description or ""))
        return []
    except RateLimitError:
        raise  # terminal rate limit — let the caller stop the run
    except Exception as e:
        print(f"LLM skill extraction failed: {e}")
        return None

def extract_role_type_llm(title, description, client):
    try:
        allowed = ", ".join(ROLE_TYPES)
        prompt = f"""Classify this job posting into exactly one role type from this list:
{allowed}

Return ONLY the role type as a single lowercase string. No explanation, no quotes, no markdown.
If the role doesn't clearly fit any technical category, return: other

Job posting:
Title: {title}
Description: {description}"""
        response = groq_chat_with_retry(
            client,
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip().lower()
        # Strip quotes or backticks the model might add despite instructions
        raw = raw.strip('"\'`')
        if raw in ROLE_TYPES:
            return raw
        # Model returned something unexpected — fall to 'other' rather than NULL
        return "other"
    except RateLimitError:
        raise  # terminal rate limit — let the caller stop the run
    except Exception as e:
        print(f"LLM role classification failed: {e}")
        return None


def extract_work_type(description):
    if not description:
        return None
    text = description.lower()
    # Check hybrid before remote because "hybrid remote" should resolve to hybrid, not remote.
    for pattern in HYBRID_PATTERNS:
        if re.search(pattern, text):
            return "hybrid"
    for pattern in REMOTE_PATTERNS:
        if re.search(pattern, text):
            return "remote"
    for pattern in ONSITE_PATTERNS:
        if re.search(pattern, text):
            return "on-site"
    return None


def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def run():
    conn = get_connection()
    cursor = conn.cursor()

    api_key = os.getenv("GROQ_API_KEY")
    groq_client = None
    if api_key:
        groq_client = Groq(api_key=api_key, timeout=30.0)
    else:
        print("GROQ_API_KEY not set — using regex fallback for skill extraction")

    # Only pull Bronze rows that haven't been transformed yet.
    cursor.execute("""
        SELECT job_id, title, description, location, company,
               salary_min, salary_max, salary_is_predicted,
               created, category, country
        FROM bronze_job_postings
        WHERE job_id NOT IN (SELECT job_id FROM silver_job_postings)
    """)
    rows = cursor.fetchall()

    if not rows:
        print("No new records to transform.")
        conn.close()
        return

    insert_query = """
        INSERT INTO silver_job_postings (
            job_id, title, description, location, company,
            salary_min, salary_max, salary_is_predicted,
            created, category, country,
            work_type, skills, skills_source, role_type, transformed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (job_id) DO NOTHING
    """

    # Set once so all rows in this batch share the same timestamp, making batch runs easy to identify.
    transformed_at = datetime.now()

    # If we burn through the daily Groq budget mid-run, stop calling the LLM and
    # finish the batch on regex. Losing a day of ingestion would be worse than
    # losing LLM enrichment on the tail of one batch; role_type stays NULL so the
    # backfill picks those rows up once the quota resets.
    llm_exhausted = False

    for row in rows:
        (job_id, title, description, location, company,
         salary_min, salary_max, salary_is_predicted,
         created, category, country) = row

        salary_is_predicted_bool = salary_is_predicted == "1" if salary_is_predicted is not None else None
        created_date = parse_date(created)
        work_type = extract_work_type(description)

        if groq_client and not llm_exhausted:
            try:
                skills = extract_skills_llm(title, description, groq_client)
                if skills is None:
                    skills = extract_skills((title or "") + " " + (description or ""))
                    skills_source = "regex"
                else:
                    skills_source = "llm"
                role_type = extract_role_type_llm(title, description, groq_client)
            except RateLimitError:
                print("Groq daily limit hit — remaining rows use regex fallback.")
                llm_exhausted = True
                skills = extract_skills((title or "") + " " + (description or ""))
                skills_source = "regex"
                role_type = None
        else:
            skills = extract_skills((title or "") + " " + (description or ""))
            skills_source = "regex"
            role_type = None

        cursor.execute(insert_query, (
            job_id, title, description, location, company,
            salary_min, salary_max, salary_is_predicted_bool,
            created_date, category, country,
            work_type, skills, skills_source, role_type, transformed_at
        ))

    conn.commit()
    print(f"{len(rows)} records transformed to Silver.")
    conn.close()


if __name__ == "__main__":
    run()
