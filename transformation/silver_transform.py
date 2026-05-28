import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.db_connection import get_connection
from datetime import datetime
import re

import json
from groq import Groq

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

def extract_skills_llm(title, description, model):
    try:
        prompt = f"""Extract all technical skills, tools, programming languages, and technologies from this job posting.
Return ONLY a JSON array of strings. No explanation, no markdown, no extra text.
Use short canonical names: "Spark" not "Apache Spark", "Kubernetes" not "k8s", "PostgreSQL" not "Postgres".
Example output: ["Python", "SQL", "AWS", "dbt"]
If none found, return: []

Job posting:
Title: {title}
Description: {description}"""
        response = model.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON array found in response: {raw[:100]}")
        skills = json.loads(match.group())
        if isinstance(skills, list):
            return [s for s in skills if isinstance(s,str)]
        return []
    except Exception as e:
        print(f"LLM skill extraction failed: {e}")
        return None

def extract_role_type_llm(title, description, model):
    try:
        allowed = ", ".join(ROLE_TYPES)
        prompt = f"""Classify this job posting into exactly one role type from this list:
{allowed}

Return ONLY the role type as a single lowercase string. No explanation, no quotes, no markdown.
If the role doesn't clearly fit any technical category, return: other

Job posting:
Title: {title}
Description: {description}"""
        response = model.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().lower()
        # Strip quotes or backticks the model might add despite instructions
        raw = raw.strip('"\'`')
        if raw in ROLE_TYPES:
            return raw
        # Model returned something unexpected — fall to 'other' rather than NULL
        return "other"
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

    for row in rows:
        (job_id, title, description, location, company,
         salary_min, salary_max, salary_is_predicted,
         created, category, country) = row

        salary_is_predicted_bool = salary_is_predicted == "1" if salary_is_predicted is not None else None
        created_date = parse_date(created)
        work_type = extract_work_type(description)

        if groq_client:
            skills = extract_skills_llm(title, description, groq_client)
            if skills is None:
                skills = extract_skills((title or "") + " " + (description or ""))
                skills_source = "regex"
            else:
                skills_source = "llm"
            role_type = extract_role_type_llm(title, description, groq_client)
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
