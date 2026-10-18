const CURRENT_ADMIN = document.body.dataset.adminEmail;
const PAGE_SIZE = 50;
let resultsOffset = 0;
let resultsTotalRows = 0;
let contactsOffset = 0;
let contactsTotal = 0;

// Stats
async function loadStats() {
    const d = await api("/api/stats");
    if (!d) return;
    const entries = [
        [d.total, "Total contacts"],
        [d.available, "Available"],
        [d.assigned, "In progress"],
        [d.done, "Done"],
        [d.answered, "Answered"],
        [d.not_answered, "No Answer"],
        [d.refused, "Refused"],
        [d.total_calls, "Total calls"],
    ];
    document.getElementById("stats-grid").innerHTML = entries.map(([n, label]) => `
    <div class="stat">
      <div class="stat-num">${n ?? "—"}</div>
      <div class="stat-label">${label}</div>
    </div>`).join("");
}

// Results
async function loadResults(offset) {
    resultsOffset = offset;
    const d = await api(`/api/results?offset=${offset}&limit=${PAGE_SIZE}`);
    if (!d) return;
    resultsTotalRows = d.total;

    const tbody = document.getElementById("results-body");
    if (!d.rows.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px;">No calls saved yet. Completed conversations will appear here.</td></tr>';
    } else {
        tbody.innerHTML = d.rows.map(r => `<tr>
      <td>${esc(r.name || "")}</td>
      <td style="white-space:nowrap;">${esc(r.phone || "")}</td>
      <td>${esc(r.volunteer_email)}</td>
      <td><span class="badge-outcome badge-${r.outcome}">${fmtOutcome(r.outcome)}</span></td>
      <td>${esc(r.comments || "")}${renderSurvey(r.survey)}</td>
      <td style="white-space:nowrap;">${fmtDate(r.submitted_at)}</td>
    </tr>`).join("");
    }

    const end = Math.min(offset + PAGE_SIZE, resultsTotalRows);
    document.getElementById("page-info").textContent =
        resultsTotalRows ? `${offset + 1}–${end} of ${resultsTotalRows}` : "0 results";
    document.getElementById("prev-btn").disabled = offset === 0;
    document.getElementById("next-btn").disabled = end >= resultsTotalRows;
}

function renderSurvey(survey) {
    return Object.values(survey || {}).map(q => '<div class="survey-answer"><b>' + esc(q.prompt) + '</b>' + esc(q.answer || 'No answer') + '</div>').join('');
}
function changePage(dir) {
    loadResults(resultsOffset + dir * PAGE_SIZE);
}

// Contacts list
async function loadContacts(offset) {
    contactsOffset = offset;
    const d = await api(`/api/contacts?offset=${offset}&limit=${PAGE_SIZE}`);
    if (!d) return;
    contactsTotal = d.total;

    const tbody = document.getElementById("contacts-body");
    if (!d.contacts.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:24px;">Your calling list is empty. Upload a CSV above to get started.</td></tr>';
    } else {
        tbody.innerHTML = d.contacts.map(c => `<tr>
      <td>${esc(c.name)}</td>
      <td style="white-space:nowrap;">${esc(c.phone)}</td>
      <td><span class="badge-outcome badge-${c.status}" style="font-size:11px;">${esc(c.status)}</span></td>
      <td>${c.call_count}</td>
      <td>${c.last_outcome ? fmtOutcome(c.last_outcome) : "—"}</td>
    </tr>`).join("");
    }

    const end = Math.min(offset + PAGE_SIZE, contactsTotal);
    document.getElementById("contacts-page-info").textContent =
        contactsTotal ? `${offset + 1}–${end} of ${contactsTotal}` : "0 contacts";
    document.getElementById("contacts-prev-btn").disabled = offset === 0;
    document.getElementById("contacts-next-btn").disabled = end >= contactsTotal;
}

function changeContactsPage(dir) {
    loadContacts(contactsOffset + dir * PAGE_SIZE);
}

// Upload
async function uploadContacts(evt) {
    evt.preventDefault();
    const file = document.getElementById("csv-file").files[0];
    if (!file) return;
    const btn = document.getElementById("upload-btn");
    btn.disabled = true;
    btn.textContent = "Uploading…";
    const form = new FormData();
    form.append("file", file);
    try {
        const r = await fetch("/api/upload", {
            method: "POST",
            credentials: "include",
            body: form
        });
        if (r.status === 401) {
            window.location.href = "/login";
            return;
        }
        const d = await r.json();
        if (r.ok && d.ok) {
            showMsg("upload-msg", "ok", `Uploaded ${d.inserted} contact${d.inserted !== 1 ? "s" : ""}.`);
            document.getElementById("upload-form").reset();
            loadStats();
            loadContacts(0);
        } else {
            showMsg("upload-msg", "err", d.detail || "Upload failed.");
        }
    } catch (_) {
        showMsg("upload-msg", "err", "Network error.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Upload contacts";
    }
}

// Callers
async function loadVolunteers() {
    const d = await api("/api/volunteers");
    if (!d) return;
    const el = document.getElementById("vol-list");
    if (!d.volunteers.length) {
        el.innerHTML = '<li style="font-size:13px;color:var(--muted);">No callers approved yet. Add your first team member above.</li>';
        return;
    }
    el.innerHTML = d.volunteers.map(v => {
        const expired = v.expires_at && new Date(v.expires_at) < new Date();
        const expiryText = v.expires_at ?
            `Expires ${fmtDate(v.expires_at)}${expired ? " (expired)" : ""}` :
            "No expiry";
        return `<li class="person-item">
      <div>
        <div class="person-email">${esc(v.email)}${expired ? ' <span style="color:#8d1f1f;font-size:11px;">(expired)</span>' : ""}</div>
        <div class="person-meta">
          Added ${fmtDate(v.added_at)}${v.added_by ? " by " + esc(v.added_by) : ""}
          &nbsp;·&nbsp; ${expiryText}
        </div>
      </div>
      <button class="btn btn-danger" data-action="remove-vol" data-email="${esc(v.email)}">Remove</button>
    </li>`;
    }).join("");
    el.querySelectorAll('[data-action="remove-vol"]').forEach(btn => {
        btn.addEventListener("click", () => removeVolunteer(btn.dataset.email));
    });
}

async function addVolunteer() {
    const emailInput = document.getElementById("new-vol");
    const expiryInput = document.getElementById("new-vol-expiry");
    const email = emailInput.value.trim();
    if (!emailInput.reportValidity() || !email) return;
    const expires_at = expiryInput.value ?
        new Date(`${expiryInput.value}T23:59:59.999`).toISOString() :
        null;
    const d = await api("/api/volunteers", {
        method: "POST",
        body: JSON.stringify({
            email,
            expires_at
        }),
    });
    if (d && d.ok) {
        emailInput.value = "";
        expiryInput.value = "";
        loadVolunteers();
        showMsg("vol-msg", "ok", "Caller approved. They can now sign in with their email.");
    } else {
        showMsg("vol-msg", "err", "Failed to add.");
    }
}

async function removeVolunteer(email) {
    if (!confirm("Remove caller " + email + "?")) return;
    const d = await api("/api/volunteers/" + encodeURIComponent(email), {
        method: "DELETE"
    });
    if (d && d.ok) loadVolunteers();
    else showMsg("vol-msg", "err", "Failed to remove.");
}

// Logout
async function doLogout() {
    await fetch("/api/logout", {
        method: "POST",
        credentials: "include"
    });
    window.location.href = "/login";
}

// Helpers
async function api(url, opts = {}) {
    try {
        const r = await fetch(url, Object.assign({
            credentials: "include",
            headers: {
                "Content-Type": "application/json"
            },
        }, opts));
        if (r.status === 401) {
            window.location.href = "/login";
            return null;
        }
        if (!r.ok) {
            const error = await r.json().catch(() => ({}));
            document.getElementById('admin-message').textContent = typeof error.detail === 'string' ? error.detail : 'Could not load this information. Please try again.';
            return null;
        }
        return await r.json();
    } catch (_) {
        document.getElementById("admin-message").textContent = "Connection lost. Check your connection and reload to try again.";
        return null;
    }
}

function showMsg(id, type, text) {
    const el = document.getElementById(id);
    el.className = "msg " + type;
    el.textContent = text;
    el.style.display = "block";

}

function esc(s) {
    return String(s || "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmtDate(iso) {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString();
    } catch (_) {
        return iso;
    }
}

function fmtOutcome(o) {
    return {
        answered: "Answered",
        not_answered: "No Answer",
        refused: "Refused"
    } [o] || o;
}

// Wire up event listeners
document.querySelector(".btn-logout").addEventListener("click", doLogout);
document.getElementById("upload-form").addEventListener("submit", uploadContacts);
document.getElementById("prev-btn").addEventListener("click", () => changePage(-1));
document.getElementById("next-btn").addEventListener("click", () => changePage(1));
document.getElementById("contacts-prev-btn").addEventListener("click", () => changeContactsPage(-1));
document.getElementById("contacts-next-btn").addEventListener("click", () => changeContactsPage(1));
document.getElementById("add-vol-btn").addEventListener("click", addVolunteer);
function showPane(name) {
    if (!['overview', 'contacts', 'results', 'volunteers'].includes(name)) name = 'overview';
    document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + name));
    document.querySelectorAll('.tab').forEach(t => {
        const current = t.dataset.pane === name;
        t.classList.toggle('active', current);
        if (current) t.setAttribute('aria-current', 'page'); else t.removeAttribute('aria-current');
    });
    history.replaceState(null, '', '#' + name);
}
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => showPane(tab.dataset.pane)));
document.querySelectorAll('[data-goto]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault(); showPane(link.dataset.goto); document.getElementById('nav').scrollIntoView({block:'start'});
}));
showPane(location.hash.slice(1));

// Boot
loadStats();
loadResults(0);
loadContacts(0);
loadVolunteers();