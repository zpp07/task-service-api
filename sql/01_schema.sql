CREATE TABLE IF NOT EXISTS practice_tasks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_key CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_practice_tasks_run_key (run_key),
    CONSTRAINT ck_practice_tasks_status CHECK (status IN ('pending', 'cancelled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS practice_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,
    kind VARCHAR(16) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY ix_practice_events_task_id_id (task_id, id),
    CONSTRAINT fk_practice_events_task FOREIGN KEY (task_id) REFERENCES practice_tasks (id),
    CONSTRAINT ck_practice_events_kind CHECK (kind IN ('created', 'cancelled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
