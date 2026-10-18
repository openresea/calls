"""Administration routes for contacts, callers, surveys, and results."""

import csv
import io
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.common.crypto import decrypt_text, encrypt_text
from app.common.db import get_ops, init_pools, purge_expired_rows, put_ops
from app.common.email_auth import normalize_email, request_code, verify_code
from app.common.security import client_ip, install_security, json_body
from app.common.survey import change_survey, load_survey
from app.common.util import fetchall_dict, fetchone_dict

logger = logging.getLogger(__name__)

ADMIN_SESSION_COOKIE = "pb_admin_session"
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_ROWS = 10_000
EXPORT_MAX_ROWS = 100_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pools()
    purge_expired_rows()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
install_security(app)
app.mount("/assets", StaticFiles(directory="app/static"), name="shared")
app.mount("/static", StaticFiles(directory="app/admin/static"), name="admin-static")
templates = Jinja2Templates(directory=["app/admin/templates", "app/templates"])


def _require_admin(req: Request) -> dict:
    token = (req.cookies.get(ADMIN_SESSION_COOKIE) or "").strip()
    if not token:
        raise HTTPException(401, "Please sign in to continue.")
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.session_token, s.admin_email
                FROM pb_admin_sessions s
                JOIN pb_admin_users u ON lower(u.email)=lower(s.admin_email)
                WHERE s.session_token = %s AND s.expires_at > now()
                """,
                (token,),
            )
            sess = fetchone_dict(cur)
    finally:
        put_ops(conn)
    if not sess:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    return sess


def _safe_csv(v: object) -> str:
    """Neutralise spreadsheet formulas."""
    s = str(v) if v is not None else ""
    return ("'" + s) if s and s[0] in "=+@-\t\r\n" else s


def _decrypt(cipher) -> str:
    try:
        return decrypt_text(cipher)
    except Exception:
        return "—"


def _iso(value):
    return value.isoformat() if value else None


def _parse_volunteer_expiry(raw: object) -> datetime | None:
    """Parse optional caller expiry input into an aware UTC datetime."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise HTTPException(400, "That expiry date doesn't look right.")
    value = raw.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.combine(date.fromisoformat(value), time.max, UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "That expiry date doesn't look right.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _result_rows(cur):
    """Decrypt result rows from RESULTS_SQL into plain dicts."""
    for (
        volunteer_email,
        outcome,
        comments,
        submitted_at,
        name_c,
        phone_c,
        answers_c,
    ) in cur:
        snapshot = (
            json.loads(decrypt_text(answers_c))
            if answers_c
            else {
                "questions": {},
                "notes": comments or "",
            }  # rows saved before encryption
        )
        yield {
            "name": _decrypt(name_c) or "Name not known",
            "phone": _decrypt(phone_c),
            "volunteer_email": volunteer_email,
            "outcome": outcome,
            "comments": snapshot["notes"],
            "survey": snapshot.get("questions", {}),
            "submitted_at": submitted_at,
        }


RESULTS_SQL = """
    SELECT r.volunteer_email, r.outcome, r.comments, r.submitted_at,
           c.name_cipher, c.phone_cipher, r.answers_cipher
    FROM pb_call_results r
    JOIN pb_contacts c ON c.contact_id = r.contact_id
    ORDER BY r.submitted_at DESC
    LIMIT %s OFFSET %s
"""


@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse("/login")


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "admin"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    try:
        _require_admin(request)
        return RedirectResponse("/dashboard")
    except HTTPException:
        return templates.TemplateResponse(request, "signin.html", {"is_admin": True})


@app.post("/api/login/request")
def api_login_request(
    request: Request, background_tasks: BackgroundTasks, body: dict = Depends(json_body)
):
    return request_code(
        body.get("email"), "admin", client_ip(request), background_tasks
    )


@app.post("/api/login/verify")
def api_login_verify(request: Request, body: dict = Depends(json_body)):
    if not body.get("code") or not body.get("challenge_id"):
        raise HTTPException(
            400, "Please enter the code from your latest sign-in email."
        )
    email = verify_code(body, "admin", client_ip(request))
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pb_admin_users WHERE lower(email)=%s", (email,))
            if not cur.fetchone():
                raise HTTPException(403, "Your organiser access has ended.")
            cur.execute(
                "INSERT INTO pb_admin_sessions(admin_email) VALUES (%s) RETURNING session_token",
                (email,),
            )
            session_token = str(cur.fetchone()[0])
    finally:
        put_ops(conn)
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=12 * 3600,
    )
    return response


@app.post("/api/logout")
def api_logout(request: Request):
    token = (request.cookies.get(ADMIN_SESSION_COOKIE) or "").strip()
    if token:
        conn = get_ops()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pb_admin_sessions WHERE session_token = %s", (token,)
                )
        finally:
            put_ops(conn)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(ADMIN_SESSION_COOKIE)
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    try:
        admin = _require_admin(request)
    except HTTPException:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request, "admin.html", {"admin_email": admin["admin_email"]}
    )


@app.get("/api/stats")
def api_stats(request: Request):
    _require_admin(request)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'available') AS available,
                    COUNT(*) FILTER (WHERE status = 'assigned')  AS assigned,
                    COUNT(*) FILTER (WHERE status = 'done')      AS done,
                    COUNT(*)                                      AS total
                FROM pb_contacts
                """
            )
            contacts = fetchone_dict(cur)
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE outcome = 'answered')     AS answered,
                    COUNT(*) FILTER (WHERE outcome = 'not_answered') AS not_answered,
                    COUNT(*) FILTER (WHERE outcome = 'refused')      AS refused,
                    COUNT(*)                                          AS total_calls
                FROM pb_call_results
                """
            )
            outcomes = fetchone_dict(cur)
    finally:
        put_ops(conn)
    return {**contacts, **outcomes}


@app.get("/api/results")
def api_results(request: Request, offset: int = 0, limit: int = 50):
    _require_admin(request)
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(RESULTS_SQL, (limit, offset))
            rows = [
                {**r, "submitted_at": _iso(r["submitted_at"])}
                for r in _result_rows(cur)
            ]
            cur.execute("SELECT COUNT(*) FROM pb_call_results")
            total = cur.fetchone()[0]
    finally:
        put_ops(conn)
    return {"total": total, "offset": offset, "rows": rows}


@app.get("/api/contacts")
def api_contacts(request: Request, offset: int = 0, limit: int = 100):
    _require_admin(request)
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT contact_id, name_cipher, phone_cipher, status,
                       call_count, last_outcome, last_called_at
                FROM pb_contacts
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = fetchall_dict(cur)
            cur.execute("SELECT COUNT(*) FROM pb_contacts")
            total = cur.fetchone()[0]
    finally:
        put_ops(conn)
    return {
        "total": total,
        "offset": offset,
        "contacts": [
            {
                "contact_id": str(r["contact_id"]),
                "name": _decrypt(r["name_cipher"]) or "Name not known",
                "phone": _decrypt(r["phone_cipher"]),
                "status": r["status"],
                "call_count": r["call_count"],
                "last_outcome": r["last_outcome"],
                "last_called_at": _iso(r["last_called_at"]),
            }
            for r in rows
        ],
    }


@app.get("/api/results/export.csv")
def export_results(request: Request):
    _require_admin(request)

    def generate():
        conn = get_ops()
        try:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(
                ["name", "phone", "caller_email", "outcome", "comments",
                 "submitted_at", "survey_answers_json"]
            )  # fmt: skip
            yield buf.getvalue().encode("utf-8-sig")
            # A named (server-side) cursor streams rows instead of loading them all.
            with conn.cursor("pb_export") as cur:
                cur.itersize = 200
                cur.execute(RESULTS_SQL, (EXPORT_MAX_ROWS, 0))
                for r in _result_rows(cur):
                    buf.seek(0)
                    buf.truncate(0)
                    w.writerow(
                        [
                            _safe_csv(v)
                            for v in (
                                r["name"], r["phone"], r["volunteer_email"],
                                r["outcome"], r["comments"], r["submitted_at"],
                                json.dumps(r["survey"], ensure_ascii=False),
                            )
                        ]
                    )  # fmt: skip
                    yield buf.getvalue().encode("utf-8")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            put_ops(conn)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pb_results.csv"},
    )


@app.post("/api/upload")
def api_upload(request: Request, file: UploadFile | None = File(None)):
    """CSV with a phone column and an optional name column; both are encrypted."""
    _require_admin(request)  # before complaining about the file, so strangers get 401
    if file is None:
        raise HTTPException(400, "Please choose a CSV file to upload.")
    raw = file.file.read(UPLOAD_MAX_BYTES + 1)
    if len(raw) > UPLOAD_MAX_BYTES:
        raise HTTPException(
            413, "That file is over 5 MB. Please split it into smaller files."
        )
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    headers = {h.strip().lower() for h in reader.fieldnames or []}
    if "phone" not in headers:
        raise HTTPException(
            400,
            'We couldn\'t find a phone column. Please add one called "phone" (name is optional).',
        )

    inserted = 0
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            for row in reader:
                row = {
                    str(k).strip().lower(): (v or "").strip()
                    for k, v in row.items()
                    if k
                }
                name, phone = row.get("name", ""), row.get("phone", "")
                if not phone:
                    continue  # nothing to call
                if len(name) > 200 or len(name.split()) > 12:
                    raise HTTPException(
                        400,
                        f'The name "{name[:40]}…" is too long. Names can be up to 200 characters.',
                    )
                if not re.fullmatch(r"\+?[0-9 ()-]{7,30}", phone):
                    raise HTTPException(
                        400,
                        f'"{phone[:30]}" doesn\'t look like a phone number. Include the country code if you can, e.g. +44 7700 900123.',
                    )
                if inserted >= MAX_UPLOAD_ROWS:
                    raise HTTPException(
                        400,
                        f"That file has more than {MAX_UPLOAD_ROWS:,} contacts. Please split it into smaller files.",
                    )
                cur.execute(
                    "INSERT INTO pb_contacts(name_cipher, phone_cipher) VALUES (%s, %s)",
                    (encrypt_text(name), encrypt_text(phone)),
                )
                inserted += 1
    finally:
        put_ops(conn)
    return {"ok": True, "inserted": inserted}


@app.get("/api/volunteers")
def api_list_volunteers(request: Request):
    _require_admin(request)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT email, added_at, added_by, expires_at "
                "FROM pb_authorized_volunteers ORDER BY added_at DESC"
            )
            rows = fetchall_dict(cur)
    finally:
        put_ops(conn)
    return {
        "volunteers": [
            {**r, "added_at": _iso(r["added_at"]), "expires_at": _iso(r["expires_at"])}
            for r in rows
        ]
    }


@app.post("/api/volunteers")
def api_add_volunteer(request: Request, body: dict = Depends(json_body)):
    admin = _require_admin(request)
    email = normalize_email(body.get("email"))
    expires_at = _parse_volunteer_expiry(body.get("expires_at"))
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pb_authorized_volunteers(email, added_by, expires_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (email) DO UPDATE
                    SET added_by = EXCLUDED.added_by, expires_at = EXCLUDED.expires_at
                """,
                (email, admin["admin_email"], expires_at),
            )
    finally:
        put_ops(conn)
    return {"ok": True}


@app.delete("/api/volunteers/{email}")
def api_remove_volunteer(email: str, request: Request):
    _require_admin(request)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM pb_authorized_volunteers WHERE lower(email) = lower(%s)",
                (email,),
            )
    finally:
        put_ops(conn)
    return {"ok": True}


@app.get("/survey", response_class=HTMLResponse)
def survey_page(request: Request):
    admin = _require_admin(request)
    return templates.TemplateResponse(
        request, "survey.html", {"admin_email": admin["admin_email"]}
    )


@app.get("/api/survey")
def survey_get(request: Request):
    _require_admin(request)
    return load_survey(archived=True)


@app.post("/api/survey")
def survey_change(request: Request, body: dict = Depends(json_body)):
    admin = _require_admin(request)
    return change_survey(body, admin["admin_email"])
