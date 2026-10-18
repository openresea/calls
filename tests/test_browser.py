"""Run with RUN_BROWSER_TESTS=1 and Playwright Chromium installed."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser tests"
)


def test_admin_editor_and_mobile_submission(live_db, monkeypatch):
    db = live_db
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 1000})

        def handle(route):
            req = route.request
            url = urlsplit(req.url)
            if url.hostname not in ("testserver", "caller.test"):
                route.abort()
                return
            client = db.caller if url.hostname == "caller.test" else db.admin
            headers = {
                k: v
                for k, v in req.headers.items()
                if k not in ("host", "cookie", "origin", "content-length")
            }
            headers["origin"] = "https://testserver"
            response = client.request(
                req.method,
                url.path + ("?" + url.query if url.query else ""),
                content=req.post_data_buffer,
                headers=headers,
            )
            route.fulfill(
                status=response.status_code,
                headers=dict(response.headers),
                body=response.content,
            )

        context.route("**/*", handle)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        from app.common import email_auth

        inbox = []
        monkeypatch.setattr(
            email_auth, "send_code", lambda email, code, purpose: inbox.append(code)
        )
        db.admin.cookies.clear()
        Path("docs/screenshots").mkdir(parents=True, exist_ok=True)
        page.goto("https://testserver/login")
        page.screenshot(path="docs/screenshots/sign-in.png", full_page=True)
        page.get_by_label("Email address", exact=True).fill("admin@example.test")
        page.get_by_role("button", name="Email me a sign-in code", exact=True).click()
        expect(page.get_by_label("6-digit sign-in code", exact=True)).to_be_visible()
        page.get_by_label("6-digit sign-in code", exact=True).fill(inbox[-1])
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("link", name="Manage survey questions")).to_be_visible()
        expect(page.locator("#stats-grid .stat")).to_have_count(8)
        page.screenshot(path="docs/screenshots/admin-dashboard.png", full_page=True)
        page.get_by_role("button", name="Contacts", exact=True).click()
        expect(
            page.get_by_role("heading", name="Add contacts", exact=True)
        ).to_be_visible()
        page.get_by_role("button", name="Callers", exact=True).click()
        expect(
            page.get_by_role("heading", name="Approve a caller", exact=True)
        ).to_be_visible()
        for width in [320, 390]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.get_by_role("button", name="Overview", exact=True).click()
        page.get_by_role("link", name="Manage survey questions").click()
        expect(page.get_by_text("No questions yet.", exact=False)).to_be_visible()
        page.get_by_label("Question", exact=True).fill("What matters to you locally?")
        page.get_by_label("Answer type").select_option("single_choice")
        page.get_by_label("Answer choices").fill("Parks\nTransport")
        page.get_by_label("Require an answer").check()
        page.get_by_role("button", name="Add question", exact=True).click()
        expect(page.get_by_text("Question added.", exact=True)).to_be_visible()
        page.get_by_role("button", name="Edit", exact=True).click()
        page.get_by_label("Question", exact=True).fill(
            "What should we improve locally?"
        )
        page.get_by_role("button", name="Save changes", exact=True).click()
        expect(page.get_by_text("Question updated.", exact=True)).to_be_visible()
        page.get_by_role("button", name="Edit", exact=True).click()
        page.get_by_role("link", name="+ Add a question", exact=True).click()
        expect(page.get_by_label("Question", exact=True)).to_have_value("")
        expect(
            page.get_by_role("button", name="Add question", exact=True)
        ).to_be_visible()
        page.get_by_role("button", name="Remove", exact=True).click()
        page.get_by_role("button", name="Remove question", exact=True).click()
        expect(page.get_by_text("Question removed.", exact=False)).to_be_visible()
        page.locator("summary").click()
        page.get_by_role("button", name="Restore", exact=True).click()
        expect(page.get_by_text("Question restored.", exact=True)).to_be_visible()
        Path("docs/screenshots").mkdir(parents=True, exist_ok=True)
        page.screenshot(path="docs/screenshots/survey-builder.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        db.caller.cookies.clear()
        page.goto("https://caller.test/pb/login")
        page.get_by_label("Email address", exact=True).fill("caller@example.test")
        page.get_by_role("button", name="Email me a sign-in code", exact=True).click()
        expect(page.get_by_label("6-digit sign-in code", exact=True)).to_be_visible()
        page.get_by_label("6-digit sign-in code", exact=True).fill(inbox[-1])
        page.get_by_role("button", name="Start calling", exact=True).click()
        page.get_by_role("button", name="Get next contact", exact=True).click()
        page.locator('[data-value="not_answered"]').click()
        expect(page.locator("#submit-btn")).to_be_enabled()
        expect(page.locator("#survey-questions")).to_be_hidden()
        page.locator('[data-value="answered"]').click()
        expect(
            page.get_by_text("What should we improve locally? (required)", exact=True)
        ).to_be_visible()
        expect(page.locator("#submit-btn")).to_be_disabled()
        expect(page.locator("#save-hint")).to_have_text(
            "Answer 1 required question to save."
        )
        expect(page.locator('[data-value="answered"]')).to_have_attribute(
            "aria-pressed", "true"
        )
        page.get_by_label("Parks", exact=True).check()
        expect(page.locator("#submit-btn")).to_be_enabled()
        page.get_by_label("Notes (optional)", exact=True).fill(
            "Fictional browser test note."
        )
        page.screenshot(path="docs/screenshots/caller-mobile.png", full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.screenshot(path="docs/screenshots/caller-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#submit-btn").click()
        expect(page.locator("#state-cooldown")).to_be_visible()
        with db.owner.cursor() as cur:
            cur.execute("SELECT count(*) FROM pb_call_results")
            assert cur.fetchone()[0] == 1
        page.goto("https://testserver/dashboard#results")
        expect(page.locator("#results-body .survey-answer b")).to_have_text(
            "What should we improve locally?"
        )
        expect(page.locator("#results-body .survey-answer")).to_contain_text("Parks")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        browser.close()
