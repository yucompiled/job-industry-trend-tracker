from transformation.silver_transform import extract_role_type_llm
import os
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

    groq_client = Groq(api_key=api_key)

    # Only process rows that haven't been classified yet.
    cursor.execute("""
        SELECT job_id, title, description
        FROM silver_job_postings
        WHERE role_type IS NULL
    """)
    rows = cursor.fetchall()
    print(f"Classifying {len(rows)} Silver rows")

    updated = 0
    failed = 0

    for job_id, title, description in rows:
        try:
            role_type = extract_role_type_llm(title, description, groq_client)
            if role_type is None:
                # LLM returned None — treat as failure, leave row NULL for next run
                failed += 1
                continue

            cursor.execute(
                "UPDATE silver_job_postings SET role_type = %s WHERE job_id = %s",
                (role_type, job_id)
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
    print(f"Done. Classified: {updated}, Skipped: {failed}")


if __name__ == "__main__":
    run()
