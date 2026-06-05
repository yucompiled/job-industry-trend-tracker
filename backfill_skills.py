from transformation.silver_transform import extract_skills_llm
import os
from dotenv import load_dotenv
from utils.db_connection import get_connection
from groq import Groq, RateLimitError
import time

# Each skill call is ~250 tokens; free-tier TPM is 6,000, so ~24 calls/min is the
# ceiling. Spacing calls ~3s apart keeps us just under it, so we rarely trip the
# per-minute limit instead of constantly bouncing off it (and burning the daily
# 14,400-request budget on rejected calls).
THROTTLE_SECONDS = 3


def run():
    load_dotenv()
    conn = get_connection()
    cursor = conn.cursor()

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY not set — cannot run backfill without LLM.")
        conn.close()
        return

    # max_retries=0: we handle retries in groq_chat_with_retry so we can tell a
    # per-minute hiccup (wait & retry) apart from a daily cap (stop the run).
    groq_client = Groq(api_key=api_key, timeout=30.0, max_retries=0)

    # Only process rows that haven't been LLM-extracted yet.
    # skills_source = 'llm' means already done; NULL means never processed here.
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
            skills = extract_skills_llm(title, description, groq_client)
        except RateLimitError:
            print(f"Daily token limit reached after {updated} rows. "
                  f"Stopping — quota resets at UTC midnight.")
            break

        if skills is None:
            # Non-rate-limit failure (e.g. malformed JSON). Leave NULL for next run.
            failed += 1
            continue

        cursor.execute(
            "UPDATE silver_job_postings SET skills = %s, skills_source = %s WHERE job_id = %s",
            (skills, 'llm', job_id)
        )
        updated += 1

        if updated % 50 == 0:
            conn.commit()
            print(f"Progress: {updated}/{len(rows)}")

        time.sleep(THROTTLE_SECONDS)

    conn.commit()
    conn.close()
    print(f"Done. LLM updated: {updated}, Skipped: {failed}")


if __name__ == "__main__":
    run()
