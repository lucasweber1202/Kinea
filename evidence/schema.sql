
CREATE TABLE IF NOT EXISTS metadata (
    series_id           VARCHAR(200) NOT NULL,
    name                VARCHAR(500) NOT NULL,
    description         VARCHAR(2000),
    country             VARCHAR(3) NOT NULL,
    frequency           VARCHAR(20),
    unit                VARCHAR(50),
    first_observation   DATE,
    last_observation    DATE,
    observation_count   INTEGER NOT NULL,
    source_url          VARCHAR(1000) NOT NULL,
    last_publish_date   DATE,
    collected_at        TIMESTAMP NOT NULL,
    CONSTRAINT pk_metadata PRIMARY KEY (series_id)
);

CREATE TABLE IF NOT EXISTS time_series (
    series_id       VARCHAR(200) NOT NULL,
    reference_date  DATE NOT NULL,
    vintage_date    DATE NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    collected_at    TIMESTAMP NOT NULL,
    CONSTRAINT pk_time_series PRIMARY KEY (series_id, reference_date, vintage_date),
    CONSTRAINT fk_time_series_metadata FOREIGN KEY (series_id)
        REFERENCES metadata(series_id)
);

CREATE TABLE IF NOT EXISTS logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP NOT NULL,
    status       VARCHAR(20) NOT NULL,
    log_text     VARCHAR(65535) NOT NULL,
    traceback    VARCHAR(65535)
);

-- Current view

SELECT series_id, reference_date, value, vintage_date, collected_at
FROM (
    SELECT t.*, ROW_NUMBER() OVER (
        PARTITION BY series_id, reference_date
        ORDER BY vintage_date DESC, collected_at DESC
    ) AS rn
    FROM time_series t
) r
WHERE rn = 1
ORDER BY series_id, reference_date

-- As-of view (bind :as_of)

SELECT series_id, reference_date, value, vintage_date, collected_at
FROM (
    SELECT t.*, ROW_NUMBER() OVER (
        PARTITION BY series_id, reference_date
        ORDER BY vintage_date DESC, collected_at DESC
    ) AS rn
    FROM time_series t
    WHERE vintage_date <= :as_of
) r
WHERE rn = 1
ORDER BY series_id, reference_date
