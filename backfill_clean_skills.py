from transformation.silver_transform import clean_skills
import os
from dotenv import load_dotenv
from utils.db_connection import get_connection

# One-off, re-runnable cleanup. Re-grounds and canonicalizes the skills already stored in Silver,
# using each row's own title + description as the source text. No LLM calls — this is pure
# post-processing on data we already have, so there are no Groq tokens or rate limits involved.
# It is idempotent: cleaning an already-clean list is a no-op, so it is safe to run repeatedly.


def run():
    load_dotenv()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT job_id, title, description, skills
        FROM silver_job_postings
        WHERE skills IS NOT NULL AND array_length(skills, 1) >= 1
    """)
    rows = cursor.fetchall()
    print(f"Re-cleaning {len(rows)} rows that have skills")

    changed = 0
    total_before = 0
    total_after = 0

    for job_id, title, description, skills in rows:
        text = (title or "") + " " + (description or "")
        cleaned = clean_skills(skills, text)
        total_before += len(skills)
        total_after += len(cleaned)

        if cleaned != skills:
            cursor.execute(
                "UPDATE silver_job_postings SET skills = %s WHERE job_id = %s",
                (cleaned, job_id)
            )
            changed += 1
            if changed % 200 == 0:
                conn.commit()
                print(f"  committed {changed} changed rows")

    conn.commit()
    conn.close()
    dropped = total_before - total_after
    print(f"Done. Rows changed: {changed}. "
          f"Skill mentions: {total_before} -> {total_after} (dropped {dropped} ungrounded/duplicate).")


if __name__ == "__main__":
    run()
