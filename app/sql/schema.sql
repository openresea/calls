-- Resea Calls database. Run once on a new, empty database as a PostgreSQL superuser.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Only approved callers can sign in to the caller app.
CREATE TABLE pb_authorized_volunteers (
    email       TEXT        PRIMARY KEY,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by    TEXT,
    expires_at  TIMESTAMPTZ             -- NULL = no expiry
);

-- Names and phone numbers are Fernet-encrypted. An empty name means "not known".
CREATE TABLE pb_contacts (
    contact_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name_cipher     TEXT        NOT NULL,
    phone_cipher    TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'available'
                                CHECK (status IN ('available', 'assigned', 'done')),
    call_count      INT         NOT NULL DEFAULT 0,
    last_outcome    TEXT        CHECK (last_outcome IN ('answered', 'not_answered', 'refused')),
    last_called_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pb_contacts_available ON pb_contacts(created_at) WHERE status = 'available';

CREATE TABLE pb_sessions (
    session_token   TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    volunteer_email TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '12 hours'
);

-- One active call per caller, session and contact. Calls expire after 30
-- minutes if the caller disappears. History also drives the 20-second cooldown.
CREATE TABLE pb_assignments (
    assignment_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id      UUID        NOT NULL REFERENCES pb_contacts(contact_id),
    session_token   TEXT        NOT NULL,
    volunteer_email TEXT        NOT NULL,
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '30 minutes',
    completed_at    TIMESTAMPTZ,
    active          BOOLEAN     NOT NULL DEFAULT TRUE
);
CREATE UNIQUE INDEX pb_assignments_one_active ON pb_assignments(session_token) WHERE active;
CREATE UNIQUE INDEX pb_assignments_one_per_caller ON pb_assignments(volunteer_email) WHERE active;
CREATE UNIQUE INDEX pb_assignments_one_per_contact ON pb_assignments(contact_id) WHERE active;
CREATE INDEX pb_assignments_caller_time ON pb_assignments(volunteer_email, assigned_at DESC);

-- Append-only. Answers and notes are encrypted in answers_cipher.
CREATE TABLE pb_call_results (
    result_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    assignment_id   UUID        NOT NULL UNIQUE REFERENCES pb_assignments(assignment_id),
    contact_id      UUID        NOT NULL REFERENCES pb_contacts(contact_id),
    volunteer_email TEXT        NOT NULL,
    outcome         TEXT        NOT NULL CHECK (outcome IN ('answered', 'not_answered', 'refused')),
    comments        TEXT,       -- only for rows saved before notes were encrypted
    answers_cipher  TEXT,
    payload_sha256  TEXT        NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pb_admin_users (
    email       TEXT        PRIMARY KEY,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by    TEXT
);

CREATE TABLE pb_admin_sessions (
    session_token   TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    admin_email     TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '12 hours'
);

CREATE TABLE survey_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    version   INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
);
INSERT INTO survey_state(singleton) VALUES (true);

CREATE TABLE survey_questions (
    question_id UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt      TEXT    NOT NULL CHECK (length(prompt) BETWEEN 1 AND 300),
    kind        TEXT    NOT NULL CHECK (kind IN ('text', 'single_choice', 'yes_no')),
    required    BOOLEAN NOT NULL DEFAULT false,
    options     JSONB   NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(options) = 'array'),
    position    INTEGER NOT NULL,
    archived    BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE survey_audit (
    audit_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_email TEXT        NOT NULL,
    action      TEXT        NOT NULL,
    question_id UUID        NOT NULL,
    version     INTEGER     NOT NULL
);

-- Results and survey history can be added to but never changed.
CREATE FUNCTION pb_call_results_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;
CREATE TRIGGER pb_call_results_no_update_delete BEFORE UPDATE OR DELETE ON pb_call_results
    FOR EACH ROW EXECUTE FUNCTION pb_call_results_immutable();
CREATE TRIGGER survey_audit_no_update_delete BEFORE UPDATE OR DELETE ON survey_audit
    FOR EACH ROW EXECUTE FUNCTION pb_call_results_immutable();

-- Sign-in codes are stored only as an HMAC. The caller table keeps the name it
-- shares with Resea Canvass.
CREATE TABLE admin_login_codes (
    challenge_id UUID        PRIMARY KEY,
    email        TEXT        NOT NULL,
    code_digest  TEXT        NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    attempts     INTEGER     NOT NULL DEFAULT 0,
    consumed     BOOLEAN     NOT NULL DEFAULT false
);
CREATE TABLE canvasser_login_codes (LIKE admin_login_codes INCLUDING ALL);
CREATE INDEX admin_login_codes_email_idx ON admin_login_codes(email);
CREATE INDEX canvasser_login_codes_email_idx ON canvasser_login_codes(email);

-- Rate-limit counters keyed by an HMAC, so no raw IPs or emails are stored.
CREATE TABLE email_auth_limits (
    key_hash  TEXT        PRIMARY KEY,
    hits      INTEGER     NOT NULL,
    resets_at TIMESTAMPTZ NOT NULL
);

-- Roles are cluster-wide, so they may already exist.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calls_admin') THEN
        CREATE ROLE calls_admin LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'calls_caller') THEN
        CREATE ROLE calls_caller LOGIN;
    END IF;
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO calls_admin, calls_caller', current_database());
END $$;
ALTER ROLE calls_admin NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE calls_caller NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO calls_admin, calls_caller;

GRANT SELECT, INSERT, DELETE ON pb_admin_users, pb_admin_sessions, pb_authorized_volunteers TO calls_admin;
GRANT UPDATE ON pb_authorized_volunteers TO calls_admin;
GRANT SELECT, INSERT ON pb_contacts TO calls_admin;
GRANT SELECT ON pb_call_results TO calls_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON admin_login_codes, email_auth_limits TO calls_admin;
GRANT SELECT, INSERT, UPDATE ON survey_state, survey_questions TO calls_admin;
GRANT SELECT, INSERT ON survey_audit TO calls_admin;

-- The caller can't read saved results back, only check its own submission's hash.
GRANT SELECT ON pb_authorized_volunteers, survey_state, survey_questions TO calls_caller;
GRANT SELECT, INSERT, DELETE ON pb_sessions TO calls_caller;
GRANT SELECT, INSERT, UPDATE, DELETE ON canvasser_login_codes, email_auth_limits TO calls_caller;
GRANT SELECT, UPDATE ON pb_contacts TO calls_caller;
GRANT SELECT, INSERT, UPDATE ON pb_assignments TO calls_caller;
GRANT INSERT ON pb_call_results TO calls_caller;
GRANT SELECT (assignment_id, payload_sha256) ON pb_call_results TO calls_caller;
