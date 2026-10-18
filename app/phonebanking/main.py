"""Caller routes for contact assignments, dialling, and survey responses.

Names and numbers are encrypted in storage. Name images and dialling redirects
do not prevent a caller from copying contact details. See SECURITY.md."""

import io
import json
import logging
import math
import textwrap
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFont

from app.common.crypto import decrypt_text, encrypt_text
from app.common.db import get_ops, init_pools, purge_expired_rows, put_ops
from app.common.email_auth import digest, request_code, verify_code
from app.common.security import client_ip, install_security, json_body
from app.common.survey import load_survey, read_survey, validate_answers
from app.common.util import fetchone_dict

logger = logging.getLogger(__name__)

PB_SESSION_COOKIE = "pb_session"
ASSIGNMENT_COOLDOWN_SECONDS = 20
UNKNOWN_NAME = "Name not known"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pools()
    purge_expired_rows()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
install_security(app)
app.mount("/assets", StaticFiles(directory="app/static"), name="shared")
app.mount(
    "/pb/static", StaticFiles(directory="app/phonebanking/static"), name="pb-static"
)
templates = Jinja2Templates(directory=["app/phonebanking/templates", "app/templates"])


def _require_pb_session(req: Request) -> dict:
    token = (req.cookies.get(PB_SESSION_COOKIE) or "").strip()
    if not token:
        raise HTTPException(401, "Please sign in to continue.")
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.session_token, s.volunteer_email
                FROM pb_sessions s
                JOIN pb_authorized_volunteers v ON lower(v.email)=lower(s.volunteer_email)
                  AND (v.expires_at IS NULL OR v.expires_at>now())
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


def _seconds_since_last_assignment(cur, email: str):
    cur.execute(
        "SELECT EXTRACT(EPOCH FROM (now() - MAX(assigned_at))) FROM pb_assignments WHERE volunteer_email=%s",
        (email,),
    )
    return cur.fetchone()[0]


# Contact names are shown as an image rather than text, to discourage copying.

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # installed in the Dockerfile
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS, for local development
)


@lru_cache(maxsize=128)
def _font(size: int):
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _make_name_image(name: str) -> bytes:
    """Render the name as large as fits, on one to three balanced lines."""
    width, height, pad_x, pad_y = 1400, 420, 24, 20
    name = " ".join((name or "").split()) or UNKNOWN_NAME
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    best = ("", _font(24), 8, 0)
    for line_count in range(1, 4):
        text = "\n".join(
            textwrap.wrap(
                name, math.ceil(len(name) / line_count), break_long_words=False
            )
        )
        if text.count("\n") >= 3:
            continue
        low, high = 24, 420
        while low <= high:  # largest font size that fits
            size = (low + high) // 2
            spacing = max(8, size // 6)
            left, top, right, bottom = draw.multiline_textbbox(
                (0, 0), text, font=_font(size), spacing=spacing, align="center"
            )
            if right - left <= width - 2 * pad_x and bottom - top <= height - 2 * pad_y:
                if size > best[3]:
                    best = (text, _font(size), spacing, size)
                low = size + 1
            else:
                high = size - 1

    text, font, spacing, _ = best
    draw.multiline_text(
        (width // 2, height // 2),
        text or name,
        fill=(15, 15, 15),
        anchor="mm",
        font=font,
        align="center",
        spacing=spacing,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _release_expired_assignments(cur) -> None:
    """Expire rows atomically before making their contacts available again."""
    cur.execute(
        "UPDATE pb_assignments SET active=false WHERE active AND expires_at<=now() RETURNING contact_id"
    )
    for (contact_id,) in cur.fetchall():
        cur.execute(
            "UPDATE pb_contacts SET status='available' WHERE contact_id=%s AND status='assigned'",
            (contact_id,),
        )


def _assigned_contact_field(request: Request, assignment_id: uuid.UUID, column: str):
    """Decrypt one field of the caller's own active contact."""
    sess = _require_pb_session(request)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.{column}
                FROM pb_assignments a
                JOIN pb_contacts c ON c.contact_id = a.contact_id
                WHERE a.assignment_id = %s AND a.session_token = %s
                  AND a.active AND a.expires_at > now()
                """,
                (str(assignment_id), sess["session_token"]),
            )
            row = cur.fetchone()
    finally:
        put_ops(conn)
    if not row:
        raise HTTPException(
            404, "This call has finished or expired. Please get the next contact."
        )
    return decrypt_text(row[0])


@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse("/pb/login")


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "caller"}


@app.get("/pb/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "signin.html", {"is_admin": False})


@app.post("/pb/api/login/request")
def api_login_request(
    request: Request, background_tasks: BackgroundTasks, body: dict = Depends(json_body)
):
    return request_code(
        body.get("email"), "caller", client_ip(request), background_tasks
    )


@app.post("/pb/api/login/verify")
def api_login_verify(request: Request, body: dict = Depends(json_body)):
    if not body.get("code") or not body.get("challenge_id"):
        raise HTTPException(
            400, "Please enter the code from your latest sign-in email."
        )
    email = verify_code(body, "caller", client_ip(request))
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pb_authorized_volunteers WHERE lower(email)=%s AND (expires_at IS NULL OR expires_at>now())",
                (email,),
            )
            if not cur.fetchone():
                raise HTTPException(
                    403,
                    "Your access has ended. Please ask an organiser to add you again.",
                )
            cur.execute(
                "INSERT INTO pb_sessions(volunteer_email) VALUES (%s) RETURNING session_token",
                (email,),
            )
            session_token = str(cur.fetchone()[0])
    finally:
        put_ops(conn)
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(
        PB_SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=12 * 3600,
    )
    return response


@app.post("/pb/api/logout")
def api_logout(request: Request):
    token = (request.cookies.get(PB_SESSION_COOKIE) or "").strip()
    if token:
        conn = get_ops()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pb_sessions WHERE session_token = %s", (token,)
                )
        finally:
            put_ops(conn)
    resp = RedirectResponse("/pb/login", status_code=303)
    resp.delete_cookie(PB_SESSION_COOKIE)
    return resp


@app.get("/pb/call", response_class=HTMLResponse)
def call_page(request: Request):
    sess = _require_pb_session(request)
    return templates.TemplateResponse(
        request, "call.html", {"volunteer_name": sess["volunteer_email"]}
    )


@app.get("/pb/api/current")
def api_current(request: Request):
    """The caller's state, without side effects: assigned, cooldown or idle."""
    sess = _require_pb_session(request)
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT assignment_id FROM pb_assignments WHERE session_token=%s AND active AND expires_at>now()",
                (sess["session_token"],),
            )
            assignment = cur.fetchone()
            seconds = _seconds_since_last_assignment(cur, sess["volunteer_email"])
    finally:
        put_ops(conn)
    if assignment:
        return {"state": "assigned", "assignment_id": str(assignment[0])}
    if seconds is not None and seconds < ASSIGNMENT_COOLDOWN_SECONDS:
        return {
            "state": "cooldown",
            "wait_seconds": ASSIGNMENT_COOLDOWN_SECONDS - int(seconds),
        }
    return {"state": "idle"}


@app.post("/pb/api/next")
def api_next(request: Request):
    """Serialize allocations per caller across sessions, then lock the contact."""
    sess = _require_pb_session(request)
    token, email = sess["session_token"], sess["volunteer_email"]
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 771))", (email,)
            )
            _release_expired_assignments(cur)
            cur.execute(
                "SELECT assignment_id,session_token FROM pb_assignments WHERE volunteer_email=%s AND active FOR UPDATE",
                (email,),
            )
            existing = fetchone_dict(cur)
            if existing:
                if existing["session_token"] != token:
                    raise HTTPException(
                        409,
                        "You've got a call open in another window. Finish it there, or wait 30 minutes for it to expire.",
                    )
                return {
                    "ok": True,
                    "assignment_id": str(existing["assignment_id"]),
                    "resumed": True,
                }
            seconds = _seconds_since_last_assignment(cur, email)
            if seconds is not None and seconds < ASSIGNMENT_COOLDOWN_SECONDS:
                return {
                    "ok": False,
                    "cooldown": True,
                    "wait_seconds": ASSIGNMENT_COOLDOWN_SECONDS - int(seconds),
                }
            cur.execute(
                "SELECT contact_id FROM pb_contacts WHERE status='available' ORDER BY created_at,contact_id LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            contact = cur.fetchone()
            if not contact:
                return {"ok": False, "exhausted": True}
            cur.execute(
                "UPDATE pb_contacts SET status='assigned' WHERE contact_id=%s",
                (contact[0],),
            )
            cur.execute(
                "INSERT INTO pb_assignments(contact_id,session_token,volunteer_email) VALUES (%s,%s,%s) RETURNING assignment_id",
                (contact[0], token, email),
            )
            assignment_id = str(cur.fetchone()[0])
    finally:
        put_ops(conn)
    return {"ok": True, "assignment_id": assignment_id, "resumed": False}


@app.get("/pb/api/name-image/{assignment_id}")
def api_name_image(assignment_id: uuid.UUID, request: Request):
    name = _assigned_contact_field(request, assignment_id, "name_cipher")
    return Response(
        content=_make_name_image(name),
        media_type="image/png",
        headers={"Cache-Control": "no-store, private"},
    )


@app.get("/pb/api/call/{assignment_id}")
def api_initiate_call(assignment_id: uuid.UUID, request: Request):
    """Redirect to tel:, so the number never appears in the page."""
    phone = _assigned_contact_field(request, assignment_id, "phone_cipher")
    safe = "".join(c for c in phone if c in "0123456789+- ")
    return RedirectResponse(f"tel:{safe}", status_code=302)


@app.post("/pb/api/submit/{assignment_id}")
def api_submit(
    assignment_id: uuid.UUID, request: Request, body: dict = Depends(json_body)
):
    """One transaction closes the assignment and stores an encrypted answer snapshot."""
    sess = _require_pb_session(request)
    outcome = body.get("outcome")
    comments = body.get("comments", "")
    if outcome not in ("answered", "not_answered", "refused"):
        raise HTTPException(400, "Please choose how the call went.")
    if not isinstance(comments, str) or len(comments) > 2000:
        raise HTTPException(400, "Notes can be up to 2,000 characters.")
    canonical = {
        "outcome": outcome,
        "comments": comments.strip(),
        "answers": body.get("answers", {}),
        "survey_version": body.get("survey_version"),
    }
    payload_hash = digest(
        f"submission:{assignment_id}:"
        + json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    )
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT contact_id,active,expires_at>now() AS fresh FROM pb_assignments WHERE assignment_id=%s AND session_token=%s FOR UPDATE",
                (str(assignment_id), sess["session_token"]),
            )
            assignment = fetchone_dict(cur)
            if not assignment:
                raise HTTPException(
                    404, "We couldn't find that call. Please get the next contact."
                )
            cur.execute(
                "SELECT payload_sha256 FROM pb_call_results WHERE assignment_id=%s",
                (str(assignment_id),),
            )
            previous = cur.fetchone()
            if previous:
                if previous[0] != payload_hash:
                    raise HTTPException(
                        409, "This call was already saved with different answers."
                    )
                return {
                    "ok": True,
                    "duplicate": True,
                    "cooldown_seconds": ASSIGNMENT_COOLDOWN_SECONDS,
                }
            if not assignment["active"] or not assignment["fresh"]:
                raise HTTPException(
                    409,
                    "This call expired before it was saved. Please get the next contact.",
                )
            validation = {
                **body,
                "notes": comments,
                "contact_status": {
                    "answered": "answered",
                    "not_answered": "no_answer",
                    "refused": "refused",
                }[outcome],
            }
            _, snapshot = validate_answers(read_survey(cur), validation)
            cur.execute(
                "INSERT INTO pb_call_results(assignment_id,contact_id,volunteer_email,outcome,answers_cipher,payload_sha256) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    str(assignment_id),
                    assignment["contact_id"],
                    sess["volunteer_email"],
                    outcome,
                    encrypt_text(json.dumps(snapshot)),
                    payload_hash,
                ),
            )
            cur.execute(
                "UPDATE pb_assignments SET active=false,completed_at=now() WHERE assignment_id=%s",
                (str(assignment_id),),
            )
            cur.execute(
                "UPDATE pb_contacts SET status='done',call_count=call_count+1,last_outcome=%s,last_called_at=now() WHERE contact_id=%s",
                (outcome, assignment["contact_id"]),
            )
    finally:
        put_ops(conn)
    return {"ok": True, "cooldown_seconds": ASSIGNMENT_COOLDOWN_SECONDS}


@app.get("/pb/api/survey")
def caller_survey(request: Request):
    _require_pb_session(request)
    return load_survey()
