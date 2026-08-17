-- botkin schema v2 (2026-06-01)
-- Один пользователь = один tenant. Без пациентов, приглашений, семей.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ============ USERS ============

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Telegram-бот создаёт пользователей с telegram_user_id; веб-регистрация — с email.
    -- Один из двух идентификаторов обязан быть не-NULL (инвариант в UserRepo).
    telegram_user_id INTEGER UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT,
    -- ролевая модель демо-уровня: admin управляет пользователями и их данными.
    -- CHECK только для свежих БД: в мигрированных колонка добавляется через ALTER без CHECK,
    -- инвариант поддерживает UserRepo.
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user')),
    display_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Сессии веб-кабинета: token_hash хранится как SHA-256 от session_token,
-- сам token отдаётся клиенту в HttpOnly cookie и не сохранится в БД.
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- ============ DOCUMENTS ============

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    doc_type TEXT CHECK(doc_type IN ('analysis','doctor_report','certificate','unknown')),
    source_path TEXT NOT NULL,
    file_sha256 TEXT,
    raw_text TEXT,
    status TEXT NOT NULL DEFAULT 'received'
        CHECK(status IN ('received','processing','recognizing','normalizing','extracted','failed')),
    confidence REAL,
    raw_extraction TEXT,
    title TEXT,
    clinic TEXT,
    delivered_at TIMESTAMP,
    -- unix-время входа в текущую стадию: достоверный прогресс-бар считает elapsed внутри стадии
    stage_started_at REAL,
    -- когда пользователь подтвердил корректность распознанных данных (NULL = не проверено)
    verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
-- лента и навигация по соседям: фильтр user_id + сортировка/сравнение по дате
CREATE INDEX IF NOT EXISTS idx_documents_user_created ON documents(user_id, created_at);

-- ============ LAB RESULTS ============

CREATE TABLE IF NOT EXISTS lab_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    analyte_code TEXT,
    analyte_name TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    unit TEXT,
    ref_low REAL,
    ref_high REAL,
    taken_at TIMESTAMP,
    source_table_cell TEXT,
    value_raw TEXT,
    unit_raw TEXT,
    taken_at_raw TEXT,
    ref_operator TEXT,
    ref_text TEXT,
    analyte_canonical TEXT,
    loinc TEXT,
    nmu_code TEXT,
    analyte_group TEXT,
    match_status TEXT,
    unit_expected TEXT,
    unit_mismatch INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lab_user_analyte ON lab_results(user_id, analyte_name, taken_at);

-- ============ DOCTOR REPORTS ============

CREATE TABLE IF NOT EXISTS doctor_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    diagnosis TEXT,
    recommendations_json TEXT,
    complaints_json TEXT,
    anamnesis TEXT,
    medications_json TEXT,
    medications_normalized_json TEXT,
    visit_date TIMESTAMP,
    doctor_name TEXT,
    department TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_doctor_reports_user ON doctor_reports(user_id, visit_date);
CREATE INDEX IF NOT EXISTS idx_doctor_reports_document ON doctor_reports(document_id);

-- ============ PATIENT FORMS (профиль тела, жалобы, текущие препараты) ============
-- Данные форм подмешиваются в контекст RAG-рекомендаций (см. rag/recommend.py).

CREATE TABLE IF NOT EXISTS patient_profile (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    sex TEXT CHECK(sex IN ('male','female')),
    birth_date TEXT,          -- ISO YYYY-MM-DD; возраст вычисляется, а не хранится
    height_cm REAL,
    weight_kg REAL,
    blood_type TEXT,          -- «O(I) Rh+» и т.п.
    allergies TEXT,
    chronic_conditions TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,       -- жалоба в свободной форме
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_patient_complaints_user
    ON patient_complaints(user_id, created_at);

CREATE TABLE IF NOT EXISTS patient_medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    dosage TEXT,              -- «500 мг», свободный формат
    schedule TEXT,            -- «2 раза в день после еды»
    is_active INTEGER NOT NULL DEFAULT 1,  -- 0 = приём завершён (история сохраняется)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_patient_medications_user
    ON patient_medications(user_id, is_active);

-- ============ HEALTH SYNC (Garmin / Strava / Apple Health) ============

-- Подключённые источники данных о здоровье. Секреты (пароль) НЕ хранятся:
-- для garmin персистятся только OAuth-токены на диске (token_path), для strava —
-- refresh-токен в token_json (OAuth-обмен), apple_health токенов не имеет (push/upload).
CREATE TABLE IF NOT EXISTS health_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL CHECK(provider IN ('garmin','strava','apple_health')),
    identifier TEXT,
    token_path TEXT,
    token_json TEXT,
    status TEXT NOT NULL DEFAULT 'connected'
        CHECK(status IN ('connected','error','disconnected')),
    last_error TEXT,
    last_sync_at TIMESTAMP,
    -- частота автосинка в часах; NULL = только вручную (см. api/routes/health_sync.py)
    sync_interval_hours INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, provider)
);

-- Универсальный time-series для метрик здоровья. value_num — скаляр (уд/мин, шаги, кг...),
-- value_json — структурные значения (фазы сна и т.п.). UNIQUE-ключ делает синк идемпотентным:
-- повторная загрузка дня перезаписывает, а не дублирует.
CREATE TABLE IF NOT EXISTS health_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    metric TEXT NOT NULL,
    taken_at TIMESTAMP NOT NULL,
    value_num REAL,
    value_json TEXT,
    unit TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, provider, metric, taken_at)
);
CREATE INDEX IF NOT EXISTS idx_health_metrics_user_metric
    ON health_metrics(user_id, metric, taken_at);

-- Активности (тренировки). external_id — id у провайдера, обеспечивает идемпотентность.
CREATE TABLE IF NOT EXISTS health_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    activity_type TEXT,
    name TEXT,
    started_at TIMESTAMP,
    duration_s REAL,
    distance_m REAL,
    avg_hr REAL,
    max_hr REAL,
    calories REAL,
    raw_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_health_activities_user
    ON health_activities(user_id, started_at);

-- ============ RAG ============

-- Чанки RAG-индекса: текст + метаданные; вектор — в vec0-таблице rag_vectors
-- (создаётся кодом, sqlite-vec это виртуальная таблица и в schema.sql ей не место:
-- расширение может быть не загружено при init_db).
CREATE TABLE IF NOT EXISTS rag_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK(source IN ('drugs','analytes','health','research')),
    user_id INTEGER,
    ref_key TEXT NOT NULL,
    text TEXT NOT NULL,
    meta_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, ref_key)
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_source ON rag_chunks(source);

-- ============ PROGRESS ============

-- EMA длительностей стадий pipeline — историческая база для оценки прогресса и ETA
-- (см. pipeline/progress_model.py).
CREATE TABLE IF NOT EXISTS stage_durations (
    stage TEXT PRIMARY KEY,
    ema_seconds REAL NOT NULL,
    samples INTEGER NOT NULL DEFAULT 0
);