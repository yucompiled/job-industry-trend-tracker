from transformation.silver_transform import extract_skills_llm
import os
import re
import json
from dotenv import load_dotenv
from utils.db_connection import get_connection
from groq import Groq
import time


def run():
    load_dotenv()
    conn = get_connection()
    cursor = conn.cursor()

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY not set — cannot run backfill without LLM.")
        conn.close()
        return

    # timeout so a hung request errors out and gets retried next run
    # instead of blocking the whole backfill indefinitely.
    groq_client = Groq(api_key=api_key, timeout=30.0)

    # Only process rows that haven't been LLM-extracted yet.
    # skills_source = 'llm' means already done — skip it.
    # NULL means never processed by this script — pick it up.
    cursor.execute("""
        SELECT job_id, title, description
        FROM silver_job_postings
        WHERE skills_source IS NULL
    """)
    rows = cursor.fetchall()
    print(f"Backfilling {len(rows)} Silver rows")

    updated = 0
    failed = 0

    for job_id, title, description in rows:
        try:
            prompt = f"""Extract all technical skills, tools, programming languages, and technologies from this job posting.
Return ONLY a JSON array of strings. No explanation, no markdown, no extra text.
Use short canonical names: "Spark" not "Apache Spark", "Kubernetes" not "k8s", "PostgreSQL" not "Postgres".
Example output: ["Python", "SQL", "AWS", "dbt"]
If none found, return: []

Job posting:
Title: {title}
Description: {description}"""

            response = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.choices[0].message.content.strip()
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if not match:
                raise ValueError(f"No JSON array found in response: {raw[:100]}")
            skills = json.loads(match.group())
            if not isinstance(skills, list):
                skills = []

            cursor.execute(
                "UPDATE silver_job_postings SET skills = %s, skills_source = %s WHERE job_id = %s",
                (skills, 'llm', job_id)
            )
            updated += 1

            if updated % 50 == 0:
                conn.commit()
                print(f"Progress: {updated}/{len(rows)}")
                time.sleep(2)

        except Exception as e:
            if "429" in str(e):
                print(f"Token limit reached after {updated} rows. Stopping — re-run tomorrow.")
                break
            print(f"Skipping job_id {job_id}: {e}")
            failed += 1

    conn.commit()
    conn.close()
    print(f"Done. LLM updated: {updated}, Skipped: {failed}")


if __name__ == "__main__":
    run()
