import json
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest

from app.common import email_auth
from app.common.crypto import decrypt_text


def add_question(db, **kw):
    current = db.admin.get("/api/survey").json()
    result = db.admin.post(
        "/api/survey",
        json={
            "version": current["version"],
            "action": "add",
            "prompt": "What matters locally?",
            "kind": "single_choice",
            "options": ["Parks", "Transport"],
            "required": True,
            **kw,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def next_call(db, client=None):
    result = (client or db.caller).post("/pb/api/next")
    assert result.status_code == 200, result.text
    return result.json()["assignment_id"]


def response_body(survey, **kw):
    return {
        "outcome": "answered",
        "survey_version": survey["version"],
        "answers": {survey["questions"][0]["id"]: "Parks"},
        "comments": "Test note",
        **kw,
    }


@pytest.fixture
def inbox(monkeypatch):
    result = []
    monkeypatch.setattr(
        email_auth,
        "send_code",
        lambda email, code, purpose: result.append((email, code, purpose)),
    )
    return result


def request_code(client, inbox, admin=True, email=None):
    email = email or ("admin@example.test" if admin else "caller@example.test")
    result = client.post(
        "/api/login/request" if admin else "/pb/api/login/request",
        json={"email": email},
    )
    assert result.status_code == 200, result.text
    return {
        "email": email,
        "code": inbox[-1][1],
        "challenge_id": result.json()["challenge_id"],
    }


def test_email_login_single_use_and_revocation(live_db, inbox):
    db = live_db
    body = request_code(db.admin, inbox)
    result = db.admin.post("/api/login/verify", json=body)
    assert result.status_code == 200, result.text
    assert (
        "Secure" in result.headers["set-cookie"]
        and "HttpOnly" in result.headers["set-cookie"]
    )
    assert db.admin.post("/api/login/verify", json=body).status_code == 401
    with db.owner, db.owner.cursor() as cur:
        cur.execute("DELETE FROM pb_admin_users")
    assert db.admin.get("/api/survey").status_code == 401


def test_wrong_code_attempts_persist(live_db, inbox):
    db = live_db
    body = request_code(db.caller, inbox, False)
    bad = "000000" if body["code"] != "000000" else "111111"
    for _ in range(5):
        assert (
            db.caller.post(
                "/pb/api/login/verify", json={**body, "code": bad}
            ).status_code
            == 401
        )
    assert db.caller.post("/pb/api/login/verify", json=body).status_code == 401
    with db.owner.cursor() as cur:
        cur.execute("SELECT attempts,consumed FROM canvasser_login_codes")
        assert cur.fetchone() == (5, True)


def test_expiry_resend_and_unknown_accounts(live_db, inbox):
    db = live_db
    body = request_code(db.admin, inbox)
    count = len(inbox)
    r = db.admin.post(
        "/api/login/request", json={"email": "unapproved@another-provider.test"}
    )
    assert r.status_code == 200 and len(inbox) == count
    assert (
        db.admin.post("/api/login/request", json={"email": body["email"]}).status_code
        == 429
    )
    with db.owner, db.owner.cursor() as cur:
        cur.execute("UPDATE admin_login_codes SET expires_at=now()-interval '1 second'")
    assert db.admin.post("/api/login/verify", json=body).status_code == 401
    with db.owner, db.owner.cursor() as cur:
        cur.execute("UPDATE email_auth_limits SET resets_at=now()-interval '1 second'")
    second = request_code(db.admin, inbox)
    assert db.admin.post("/api/login/verify", json=body).status_code == 401
    assert db.admin.post("/api/login/verify", json=second).status_code == 200


def test_caller_revocation_and_expiry_block_existing_sessions(live_db):
    db = live_db
    assert db.caller.get("/pb/api/survey").status_code == 200
    with db.owner, db.owner.cursor() as cur:
        cur.execute(
            "UPDATE pb_authorized_volunteers SET expires_at=now()-interval '1 second' WHERE email='caller@example.test'"
        )
    assert db.caller.get("/pb/api/current").status_code == 401
    assert db.second.post("/pb/api/next").status_code == 401
    assert db.admin.delete("/api/volunteers/other@example.test").status_code == 200
    assert db.other.get("/pb/api/survey").status_code == 401


def test_survey_lifecycle_and_encrypted_history(live_db):
    db = live_db
    survey = add_question(db)
    qid = survey["questions"][0]["id"]
    assignment = next_call(db)
    body = response_body(survey)
    result = db.caller.post("/pb/api/submit/" + assignment, json=body)
    assert result.status_code == 200, result.text
    assert db.caller.post("/pb/api/submit/" + assignment, json=body).json()["duplicate"]
    assert (
        db.caller.post(
            "/pb/api/submit/" + assignment, json={**body, "comments": "Different"}
        ).status_code
        == 409
    )
    with db.owner.cursor() as cur:
        cur.execute("SELECT comments,answers_cipher FROM pb_call_results")
        rows = cur.fetchall()
        assert len(rows) == 1 and rows[0][0] is None and "Parks" not in rows[0][1]
        assert (
            json.loads(decrypt_text(rows[0][1]))["questions"][qid]["prompt"]
            == "What matters locally?"
        )
    removed = db.admin.post(
        "/api/survey",
        json={"action": "archive", "id": qid, "version": survey["version"]},
    )
    assert removed.status_code == 200
    assert db.caller.get("/pb/api/survey").json()["questions"] == []
    restored = db.admin.post(
        "/api/survey",
        json={"action": "restore", "id": qid, "version": removed.json()["version"]},
    )
    assert restored.status_code == 200
    exported = db.admin.get("/api/results/export.csv")
    assert (
        exported.status_code == 200
        and "What matters locally?" in exported.text
        and "Parks" in exported.text
    )
    assert (
        db.admin.get("/api/results").json()["rows"][0]["survey"][qid]["answer"]
        == "Parks"
    )
    assert db.caller.post("/pb/api/submit/" + assignment, json=body).json()["duplicate"]


def test_stale_survey_and_required_answers(live_db):
    db = live_db
    s = add_question(db)
    assignment = next_call(db)
    assert (
        db.caller.post(
            "/pb/api/submit/" + assignment, json=response_body(s, answers={})
        ).status_code
        == 400
    )
    add_question(db, prompt="Optional note?", kind="text", required=False)
    assert (
        db.caller.post(
            "/pb/api/submit/" + assignment, json=response_body(s)
        ).status_code
        == 409
    )


@pytest.mark.parametrize("outcome", ["not_answered", "refused"])
def test_non_conversation_outcomes(live_db, outcome):
    db = live_db
    s = add_question(db)
    assignment = next_call(db)
    assert (
        db.caller.post(
            "/pb/api/submit/" + assignment,
            json=response_body(s, outcome=outcome, answers={}),
        ).status_code
        == 200
    )


def test_cross_session_access_is_denied_and_get_cannot_allocate(live_db):
    db = live_db
    assignment = next_call(db)
    assert db.caller.get("/pb/api/next").status_code == 405
    assert db.second.post("/pb/api/next").status_code == 409
    assert db.other.get("/pb/api/name-image/" + assignment).status_code == 404
    assert (
        db.other.get("/pb/api/call/" + assignment, follow_redirects=False).status_code
        == 404
    )
    assert (
        db.other.post(
            "/pb/api/submit/" + assignment, json={"outcome": "refused"}
        ).status_code
        == 404
    )
    assert (
        db.caller.get("/pb/api/name-image/" + assignment).headers["content-type"]
        == "image/png"
    )
    # httpx's high-level client tries to construct an HTTP redirect for tel: even with redirects disabled.
    dial = db.caller._transport.handle_request(
        db.caller.build_request("GET", "/pb/api/call/" + assignment)
    )
    dial.read()
    assert dial.headers["location"] == "tel:+447700900001"
    assert dial.headers["cache-control"] == "no-store"


def test_concurrent_allocation_and_cooldown(live_db):
    db = live_db
    with ThreadPoolExecutor(max_workers=2) as ex:
        responses = list(
            ex.map(lambda client: client.post("/pb/api/next"), [db.caller, db.second])
        )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assignment = next(
        r.json()["assignment_id"] for r in responses if r.status_code == 200
    )
    client = db.caller if responses[0].status_code == 200 else db.second
    survey = client.get("/pb/api/survey").json()
    assert (
        client.post(
            "/pb/api/submit/" + assignment,
            json={
                "outcome": "refused",
                "survey_version": survey["version"],
                "answers": {},
            },
        ).status_code
        == 200
    )
    assert client.post("/pb/api/next").json()["cooldown"]
    with db.owner.cursor() as cur:
        cur.execute("SELECT count(*) FROM pb_assignments")
        assert cur.fetchone()[0] == 1


def test_expired_assignment_released_and_old_session_cannot_submit(live_db):
    db = live_db
    assignment = next_call(db)
    with db.owner, db.owner.cursor() as cur:
        cur.execute("UPDATE pb_assignments SET expires_at=now()-interval '1 second'")
    assert (
        db.caller.post(
            "/pb/api/submit/" + assignment, json={"outcome": "refused"}
        ).status_code
        == 409
    )
    assert db.other.post("/pb/api/next").status_code == 200
    with db.owner.cursor() as cur:
        cur.execute(
            "SELECT active FROM pb_assignments WHERE assignment_id=%s", (assignment,)
        )
        assert cur.fetchone()[0] is False


def test_database_privilege_boundaries(live_db):
    db = live_db
    conn = db.pools[1].getconn()
    try:
        for query in [
            "SELECT * FROM admin_login_codes",
            "SELECT * FROM pb_admin_sessions",
            "SELECT * FROM pb_call_results",
            "UPDATE survey_state SET version=99",
            "INSERT INTO pb_admin_users(email) VALUES ('intruder@example.test')",
        ]:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                with conn, conn.cursor() as cur:
                    cur.execute(query)
    finally:
        db.pools[1].putconn(conn)
    assert db.caller.get("/api/survey").status_code == 404


def test_csv_import_and_wrong_origin(live_db):
    db = live_db
    assert (
        db.admin.post(
            "/api/upload",
            files={
                "file": (
                    "contacts.csv",
                    "name,phone\nAnother Contact,+447700900002",
                    "text/csv",
                )
            },
        ).status_code
        == 200
    )
    assert (
        db.admin.post(
            "/api/upload", files={"file": ("wrong.csv", "bad,headers\nx,y", "text/csv")}
        ).status_code
        == 400
    )
    assert (
        db.admin.post(
            "/api/survey", json={}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        db.admin.post(
            "/api/survey", json={}, headers={"Origin": "http://testserver"}
        ).status_code
        == 403
    )
    assert db.admin.get("/api/survey", headers={"Host": "evil.test"}).status_code == 400


def test_contacts_without_names_and_sample_file(live_db):
    db = live_db
    r = db.admin.post(
        "/api/upload",
        files={
            "file": (
                "contacts.csv",
                "Name,Phone\n,+447700900010\nPat Example,+447700900011\nNo Number,\n",
                "text/csv",
            )
        },
    )
    assert (
        r.status_code == 200 and r.json()["inserted"] == 2
    )  # row with no number skipped
    names = {c["name"] for c in db.admin.get("/api/contacts").json()["contacts"]}
    assert {"Name not known", "Pat Example"} <= names
    with open("samples/contacts.example.csv", "rb") as f:
        r = db.admin.post("/api/upload", files={"file": ("sample.csv", f, "text/csv")})
    assert r.status_code == 200, r.text
    missing = db.admin.post(
        "/api/upload", files={"file": ("x.csv", "name\nPat\n", "text/csv")}
    )
    assert missing.status_code == 400 and "phone column" in missing.json()["detail"]


def test_code_consumed_once_under_concurrency(live_db, inbox):
    body = request_code(live_db.caller, inbox, False)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: live_db.caller.post("/pb/api/login/verify", json=body),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 401]


def test_concurrent_submission_saves_one_result(live_db):
    survey = add_question(live_db)
    assignment = next_call(live_db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: live_db.caller.post(
                    "/pb/api/submit/" + assignment, json=response_body(survey)
                ),
                range(2),
            )
        )
    assert [r.status_code for r in responses] == [200, 200]
    assert sum(bool(r.json().get("duplicate")) for r in responses) == 1
    with live_db.owner.cursor() as cur:
        cur.execute("SELECT count(*) FROM pb_call_results")
        assert cur.fetchone()[0] == 1


def test_logout_does_not_reset_caller_cooldown(live_db):
    assignment = next_call(live_db)
    version = live_db.caller.get("/pb/api/survey").json()["version"]
    assert (
        live_db.caller.post(
            "/pb/api/submit/" + assignment,
            json={"outcome": "refused", "survey_version": version, "answers": {}},
        ).status_code
        == 200
    )
    assert live_db.caller.post("/pb/api/logout").status_code == 200
    assert live_db.second.get("/pb/api/current").json()["state"] == "cooldown"
    assert live_db.second.post("/pb/api/next").json()["cooldown"]


@pytest.mark.parametrize("admin", [True, False])
def test_startup_cleanup_uses_service_permissions(live_db, monkeypatch, admin):
    from app.common import db

    monkeypatch.setenv(
        "APP_MODULE", "app.admin.main:app" if admin else "app.phonebanking.main:app"
    )
    db._ops_pool = live_db.pools[0 if admin else 1]
    db.purge_expired_rows()
