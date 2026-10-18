-- Resea Calls schema (add to OPS_DB)
-- Requires: pgcrypto extension

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Authorized volunteers
-- Only emails in this table can log into the caller app.
CREATE TABLE pb_authorized_volunteers (
    email       TEXT        PRIMARY KEY,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by    TEXT,
    expires_at  TIMESTAMPTZ             -- NULL = no expiry
);

-- Contacts
-- Names and phone numbers are Fernet-encrypted at rest.
-- Contacts are served one at a time; status tracks their lifecycle.
CREATE TABLE pb_contacts (
    contact_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name_cipher     TEXT        NOT NULL,   -- Fernet-encrypted full name
    phone_cipher    TEXT        NOT NULL,   -- Fernet-encrypted phone number
    status          TEXT        NOT NULL DEFAULT 'available'
                                CHECK (status IN ('available', 'assigned', 'done')),
    call_count      INT         NOT NULL DEFAULT 0,
    last_outcome    TEXT        CHECK (last_outcome IN ('answered', 'not_answered', 'refused')),
    last_called_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only index available contacts so the assignment query is fast
CREATE INDEX pb_contacts_available ON pb_contacts(created_at)
    WHERE status = 'available';

-- Volunteer sessions
-- 12-hour cookie-backed sessions.
-- last_assigned_at drives the 20-second cooldown between contacts.
CREATE TABLE pb_sessions (
    session_token       TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    volunteer_email     TEXT        NOT NULL,
    display_name        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '12 hours',
    last_assigned_at    TIMESTAMPTZ  -- updated when a new contact is assigned
);

-- Assignments
-- One active assignment per session at a time (unique index enforces this).
-- Assignments expire after 30 minutes if the caller disappears.
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

-- Enforces at most one active assignment per session
CREATE UNIQUE INDEX pb_assignments_one_active
    ON pb_assignments(session_token)
    WHERE active = TRUE;

CREATE INDEX pb_assignments_contact ON pb_assignments(contact_id);

-- Call results (append-only)
-- Outcomes and comments. Never updated or deleted.
CREATE TABLE pb_call_results (
    result_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    assignment_id   UUID        NOT NULL REFERENCES pb_assignments(assignment_id),
    contact_id      UUID        NOT NULL REFERENCES pb_contacts(contact_id),
    volunteer_email TEXT        NOT NULL,
    outcome         TEXT        NOT NULL
                    CHECK (outcome IN ('answered', 'not_answered', 'refused')),
    comments        TEXT, -- reserved for imported legacy rows; new notes are encrypted
    answers_cipher  TEXT,
    payload_sha256  TEXT NOT NULL,
    UNIQUE (assignment_id),
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Admin users & sessions
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

-- Prevent modification of results
CREATE OR REPLACE FUNCTION pb_call_results_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'pb_call_results is append-only';
END;
$$;

CREATE TRIGGER pb_call_results_no_update_delete
    BEFORE UPDATE OR DELETE ON pb_call_results
    FOR EACH ROW EXECUTE FUNCTION pb_call_results_immutable();

CREATE UNIQUE INDEX pb_assignments_one_per_caller ON pb_assignments(volunteer_email) WHERE active;
CREATE UNIQUE INDEX pb_assignments_one_per_contact ON pb_assignments(contact_id) WHERE active;
