UPDATE gold_role_demand grd
SET new_postings_today = sub.count
FROM (
    SELECT
        DATE(ingested_at) AS ingestion_date,
        category,
        country,
        COUNT(*) AS count
    FROM bronze_job_postings
    GROUP BY DATE(ingested_at), category, country
) AS sub
WHERE grd.snapshot_date = sub.ingestion_date
  AND grd.category = sub.category
  AND grd.country = sub.country;