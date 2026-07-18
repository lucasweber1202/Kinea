WITH ranked AS (
    SELECT t.*,
           ROW_NUMBER() OVER (
               PARTITION BY series_id, reference_date
               ORDER BY vintage_date DESC, collected_at DESC
           ) AS rn
      FROM time_series t
), latest_three AS (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY series_id ORDER BY reference_date DESC
           ) AS recency
      FROM ranked r
     WHERE rn = 1
)
SELECT m.series_id, m.name, m.frequency, m.unit,
       l.reference_date, l.value, l.vintage_date
  FROM latest_three l
  JOIN metadata m ON m.series_id = l.series_id
 WHERE l.recency <= 3
 ORDER BY m.series_id, l.reference_date DESC;
