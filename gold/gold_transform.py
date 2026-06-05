import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.db_connection import get_connection
from datetime import datetime, date


# Skills are only meaningful for tech/data roles: our skill vocabulary is tech-focused, so
# engineering, finance, and accounting roles produce near-empty or noisy skill data. We aggregate
# the skills-by-role table only for these roles. Every role is still tracked for demand and salary.
SKILL_ROLE_TYPES = (
    'data_engineering', 'data_science', 'ml_engineering', 'data_analytics',
    'software_engineering', 'devops', 'security_engineering', 'mobile_engineering',
)


def run():
    conn = get_connection()
    cursor = conn.cursor()

    # Each Gold run represents a daily snapshot. snapshot_date is today's date and is used
    # as the primary key component across all three Gold tables.
    snapshot_date = date.today()

    # Delete today's existing rows before inserting so the pipeline is idempotent.
    # If it runs twice in one day, the second run always reflects the fuller dataset.
    cursor.execute("DELETE FROM gold_role_demand WHERE snapshot_date = %s", (snapshot_date,))
    cursor.execute("DELETE FROM gold_salary_trend WHERE snapshot_date = %s", (snapshot_date,))
    cursor.execute("DELETE FROM gold_skill_frequency WHERE snapshot_date = %s", (snapshot_date,))
    cursor.execute("DELETE FROM gold_role_type_skill_frequency WHERE snapshot_date = %s", (snapshot_date,))

    # Daily counts: how many new postings were ingested into Bronze today, grouped by category and country.
    cursor.execute("""
        SELECT category, country, COUNT(*)
        FROM bronze_job_postings
        WHERE DATE(ingested_at) = %s
        GROUP BY category, country
    """, (snapshot_date,))
    daily_count_results = cursor.fetchall()
    daily_counts = {(category, country): count for category, country, count in daily_count_results}

    # Role demand: total job postings per category and country as of today.
    # We store results in a dict so skill frequency can look up the totals without a second query.
    select_query_role_demand = "SELECT category, country, COUNT(*) FROM silver_job_postings GROUP BY category, country"
    cursor.execute(select_query_role_demand)
    role_demand_results = cursor.fetchall()
    role_demand_totals = {(category, country): total_postings for category, country, total_postings in role_demand_results}

    insert_query_role_demand = "INSERT INTO gold_role_demand (snapshot_date, category, country, total_postings, new_postings_today, aggregated_at) VALUES (%s, %s, %s, %s, %s, %s)"
    for category, country, total_postings in role_demand_results:
        new_postings_today = daily_counts.get((category, country), 0)
        cursor.execute(insert_query_role_demand, (snapshot_date, category, country, total_postings, new_postings_today, datetime.now()))

    # Salary trends: averages across postings that have both salary_min and salary_max.
    # posting_count_predicted tracks how many salaries are Adzuna estimates vs employer-reported.
    # We keep predicted salaries in rather than filtering them out because Adzuna's methodology
    # is statistically sound and discarding them would shrink an already small sample.
    select_query_salary = """
        SELECT category, country,
               AVG(salary_min), AVG(salary_max),
               AVG((salary_min + salary_max) / 2),
               COUNT(*),
               COUNT(*) FILTER (WHERE salary_is_predicted = true)
        FROM silver_job_postings
        WHERE salary_min IS NOT NULL AND salary_max IS NOT NULL
        GROUP BY category, country
    """
    cursor.execute(select_query_salary)
    salary_results = cursor.fetchall()


    insert_query_salary = "INSERT INTO gold_salary_trend (snapshot_date, category, country, avg_salary_min, avg_salary_max, avg_midpoint, total_salary_postings, posting_count_predicted, aggregated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    for category, country, avg_salary_min, avg_salary_max, avg_midpoint, total_salary_postings, posting_count_predicted in salary_results:
        cursor.execute(insert_query_salary, (snapshot_date, category, country, avg_salary_min, avg_salary_max, avg_midpoint, total_salary_postings, posting_count_predicted, datetime.now()))

    # Skill frequency: count how many postings mention each skill, then calculate the percentage
    # against the total postings for that country/category bucket.
    # skill_posting_count is a lower bound because Adzuna truncates descriptions at around 500 characters.
    select_query_skills = "SELECT unnest(skills) AS skill, country, category, COUNT(*) FROM silver_job_postings GROUP BY skill, country, category"
    cursor.execute(select_query_skills)
    skill_results = cursor.fetchall()

    insert_query_skills = "INSERT INTO gold_skill_frequency (snapshot_date, skill, country, category, skill_posting_count, total_postings, pct_of_postings, aggregated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    for skill, country, category, skill_posting_count in skill_results:
        total_postings = role_demand_totals.get((category, country), 0)
        pct_of_postings = (skill_posting_count / total_postings) * 100 if total_postings > 0 else 0
        cursor.execute(insert_query_skills, (snapshot_date, skill, country, category, skill_posting_count, total_postings, pct_of_postings, datetime.now()))

    # Skill frequency BY ROLE TYPE: same idea as above, grouped by the LLM-classified role_type
    # (data_engineering, software_engineering, ...) instead of Adzuna's broad category.
    # We restrict to SKILL_ROLE_TYPES — the tech/data roles — because our skill vocabulary is
    # tech-focused, so engineering/finance/accounting roles (and the 'other' grab bag) produce
    # near-empty or noisy skill data. Those roles are still tracked for demand and salary.
    cursor.execute("""
        SELECT role_type, country, COUNT(*)
        FROM silver_job_postings
        WHERE role_type IN %s
        GROUP BY role_type, country
    """, (SKILL_ROLE_TYPES,))
    role_type_totals = {(role_type, country): total for role_type, country, total in cursor.fetchall()}

    cursor.execute("""
        SELECT unnest(skills) AS skill, country, role_type, COUNT(*)
        FROM silver_job_postings
        WHERE role_type IN %s
        GROUP BY skill, country, role_type
    """, (SKILL_ROLE_TYPES,))
    role_type_skill_results = cursor.fetchall()

    insert_query_role_type_skills = "INSERT INTO gold_role_type_skill_frequency (snapshot_date, skill, country, role_type, skill_posting_count, total_postings, pct_of_postings, aggregated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    for skill, country, role_type, skill_posting_count in role_type_skill_results:
        total_postings = role_type_totals.get((role_type, country), 0)
        pct_of_postings = (skill_posting_count / total_postings) * 100 if total_postings > 0 else 0
        cursor.execute(insert_query_role_type_skills, (snapshot_date, skill, country, role_type, skill_posting_count, total_postings, pct_of_postings, datetime.now()))



    conn.commit()
    print(f"Gold tables updated for snapshot date: {snapshot_date}")
    conn.close()


if __name__ == "__main__":
    run()
