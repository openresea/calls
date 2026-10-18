let survey = null;
let submitting = false;
// State machine
let currentAssignmentId = null;
let selectedOutcome = null;
let cooldownTimer = null;
const COOLDOWN = 20;
const CIRCUMFERENCE = 238.76;

const STATES = ["idle", "loading", "assigned", "cooldown", "exhausted"];

function setState(name) {
    STATES.forEach(s => {
        document.getElementById("state-" + s).classList.toggle("active", s === name);
    });
}

// Boot: restore state after page refresh
(async function boot() {
    setState("loading");
    try {
        const r = await apiFetch("/pb/api/current");
        if (!r) { setState("idle"); return; }
        if (r.state === "assigned") {
            currentAssignmentId = r.assignment_id;
            showAssigned();
        } else if (r.state === "cooldown") {
            startCooldown(r.wait_seconds);
        } else {
            setState("idle");
        }
    } catch (e) {
        setState("idle");
    }
})();

// Get next contact
async function getNextContact() {
    setState("loading");
    const r = await apiFetch("/pb/api/next", {
        method: "POST"
    });
    if (!r) {
        setState("idle");
        return;
    }
    if (r.ok) {
        currentAssignmentId = r.assignment_id;
        showAssigned();
    } else if (r.cooldown) {
        startCooldown(r.wait_seconds);
    } else if (r.exhausted) {
        setState("exhausted");
    } else {
        setState("idle");
    }
}

// Show assigned state
async function showAssigned() {
    selectedOutcome = null;
    survey = null;
    document.getElementById("outcome-help").textContent = "";
    document.getElementById("survey-questions").replaceChildren();
    document.querySelectorAll(".outcome-btn").forEach(b => { b.classList.remove("selected"); b.setAttribute("aria-pressed", "false"); });
    document.getElementById("comments").value = "";
    document.getElementById("submit-error").style.display = "none";
    updateSaveState();

    const img = document.getElementById("name-img");
    img.src = "";
    img.src = "/pb/api/name-image/" + currentAssignmentId;

    setState("assigned");
    await loadSurvey();
}

// Outcome selection
function selectOutcome(btn) {
    document.querySelectorAll(".outcome-btn").forEach(b => { b.classList.remove("selected"); b.setAttribute("aria-pressed", "false"); });
    btn.classList.add("selected");
    btn.setAttribute("aria-pressed", "true");
    selectedOutcome = btn.getAttribute("data-value");
    document.getElementById("outcome-help").textContent = selectedOutcome === "answered" ? "Ask for permission before recording answers." : "You can save this outcome without survey answers.";
    document.getElementById("survey-questions").hidden = selectedOutcome !== "answered";
    document.getElementById("submit-error").style.display = "none";
    updateSaveState();
}

// Initiate call (server-side redirect to tel:)
function initiateCall(evt) {
    evt.preventDefault();
    window.location.href = "/pb/api/call/" + currentAssignmentId;
}

// Submit outcome
async function submitOutcome() {
    if (submitting) return;
    if (!survey) {
        showSubmitError("Load the survey questions before saving.");
        return;
    }
    if (!selectedOutcome) {
        showSubmitError("Please select an outcome first.");
        return;
    }
    const btn = document.getElementById("submit-btn");
    btn.disabled = true;
    submitting = true;
    updateSaveState();

    const comments = document.getElementById("comments").value.trim();
    const r = await apiFetch(
        "/pb/api/submit/" + currentAssignmentId, {
            method: "POST",
            body: JSON.stringify({
                outcome: selectedOutcome,
                comments,
                survey_version: survey.version,
                answers: collectAnswers()
            })
        }
    );
    btn.disabled = false;
    submitting = false;
    updateSaveState();

    if (!r) return;
    if (!r.ok) {
        showSubmitError("Submission failed \u2014 please try again.");
        return;
    }

    document.getElementById("name-img").src = "";
    currentAssignmentId = null;
    await getNextContact();
    document.getElementById("api-message").className = "call-status ok";
    document.getElementById("api-message").textContent = "Call saved. Thank you for recording the conversation.";
}

function showSubmitError(msg) {
    const el = document.getElementById("submit-error");
    el.textContent = msg;
    el.style.display = "block";
}

// Cooldown countdown
function startCooldown(seconds) {
    if (cooldownTimer) {
        clearInterval(cooldownTimer);
        cooldownTimer = null;
    }
    let remaining = Math.max(1, Math.ceil(seconds));
    setState("cooldown");
    updateCooldown(remaining);

    cooldownTimer = setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
            clearInterval(cooldownTimer);
            cooldownTimer = null;
            getNextContact();
        } else {
            updateCooldown(remaining);
        }
    }, 1000);
}

function updateCooldown(remaining) {
    document.getElementById("countdown-num").textContent = remaining;
    document.getElementById("countdown-label").textContent = remaining;

    const pct = remaining / COOLDOWN;
    const offset = CIRCUMFERENCE * (1 - pct);
    document.getElementById("ring-fill").style.strokeDashoffset = offset;

    const btn = document.getElementById("cooldown-btn");
    btn.disabled = remaining > 0;
}

// Logout
async function logout() {
    await fetch("/pb/api/logout", {
        method: "POST",
        credentials: "include"
    });
    window.location.href = "/pb/login";
}

// Fetch helper
async function apiFetch(url, options = {}) {
    const defaults = {
        credentials: "include",
        headers: {
            "Content-Type": "application/json"
        },
    };
    try {
        const r = await fetch(url, Object.assign({}, defaults, options,
            options.headers ? {
                headers: Object.assign({}, defaults.headers, options.headers)
            } : {}));
        if (r.status === 401) {
            window.location.href = "/pb/login";
            return null;
        }
        if (!r.ok) {
            let detail = 'Request failed. Please try again.';
            try {
                const body = await r.json();
                if (typeof body.detail === 'string') detail = body.detail;
            } catch (_) {}
            document.getElementById('api-message').className = 'call-status error';
            document.getElementById('api-message').textContent = detail;
            if (url.includes('/submit/')) showSubmitError(detail);
            return null;
        }
        document.getElementById('api-message').textContent = '';
        return await r.json();
    } catch (e) {
        document.getElementById('api-message').className = 'call-status error';
        document.getElementById('api-message').textContent = 'Connection lost. Your answers are still here. Please retry.';
        if (url.includes('/submit/')) showSubmitError('Connection lost. Your answers are still here. Please retry.');
        return null;
    }
}

// Event listeners (replacing inline onclick/oncontextmenu attributes)
document.querySelector(".btn-logout").addEventListener("click", logout);
document.querySelector("#state-idle .btn-primary").addEventListener("click", getNextContact);
document.getElementById("cooldown-btn").addEventListener("click", getNextContact);
document.querySelectorAll(".outcome-btn").forEach(btn =>
    btn.addEventListener("click", function() {
        selectOutcome(this);
    })
);
document.getElementById("call-btn").addEventListener("click", initiateCall);
document.getElementById("submit-btn").addEventListener("click", submitOutcome);
document.getElementById("name-img").addEventListener("contextmenu", e => e.preventDefault());


function collectAnswers() {
    if (selectedOutcome !== 'answered') return {};
    const answers = {};
    document.querySelectorAll('[data-question-id]').forEach(input => {
        if ((input.type !== 'radio' || input.checked) && input.value.trim()) answers[input.dataset.questionId] = input.value.trim();
    });
    return answers;
}
async function loadSurvey() {
    const old = collectAnswers();
    const next = await apiFetch('/pb/api/survey');
    if (!next) return;
    survey = next;
    const target = document.getElementById('survey-questions');
    target.replaceChildren();
    for (const q of survey.questions) {
        const field = document.createElement('fieldset');
        const legend = document.createElement('legend');
        legend.textContent = q.prompt + (q.required ? ' (required)' : ' (optional)');
        field.append(legend);
        if (q.kind === 'text') {
            const input = document.createElement('textarea');
            input.dataset.questionId = q.id;
            input.maxLength = 2000;
            input.value = old[q.id] || '';
            input.setAttribute('aria-label', q.prompt);
            field.append(input);
        } else {
            for (const value of q.options) {
                const label = document.createElement('label');
                const input = document.createElement('input');
                input.type = 'radio';
                input.name = q.id;
                input.dataset.questionId = q.id;
                input.value = value;
                input.checked = old[q.id] === value;
                label.append(input, document.createTextNode(value));
                field.append(label);
            }
        }
        target.append(field);
    }
    target.hidden = selectedOutcome !== "answered";
    document.getElementById('question-count').textContent = survey.questions.length + (survey.questions.length === 1 ? ' survey question' : ' survey questions');
    updateSaveState();
}
document.getElementById('reload-survey').addEventListener('click', loadSurvey);
function updateSaveState() {
    const answers = collectAnswers();
    const missing = survey && selectedOutcome === 'answered' ? survey.questions.filter(q => q.required && !answers[q.id]).length : 0;
    const ready = survey && selectedOutcome && !missing && !submitting;
    const button = document.getElementById('submit-btn');
    button.disabled = !ready;
    button.textContent = submitting ? 'Saving…' : 'Save & next contact';
    document.getElementById('save-hint').textContent = submitting ? 'Saving your conversation…' : !survey ? 'Loading your team’s questions…' : !selectedOutcome ? 'Choose an outcome to continue.' : missing ? `Answer ${missing} required question${missing === 1 ? '' : 's'} to save.` : 'Ready to save. Your next contact follows automatically.';
}
document.getElementById('survey-questions').addEventListener('input', updateSaveState);
document.getElementById('check-contacts').addEventListener('click', getNextContact);
