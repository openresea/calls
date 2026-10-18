# Resea Calls

A free, self-hosted phone banking app for volunteer calling. It's built for charities, community groups and campaigns: upload a list of people to call, approve your volunteers, and each caller gets one contact at a time and records the conversation and survey answers on their phone or computer. You host it yourself, so your data stays with you.

It's free for any non-commercial use, and all the code is public. Have a look at the [screenshots](docs/screenshots.md).

Source: [openresea/calls](https://github.com/openresea/calls) · Questions: [resea@tuta.io](mailto:resea@tuta.io)

![Survey question editor](docs/screenshots/survey-builder.png)

## What it does

- **Sign in with email.** Callers sign in with any email address and a six-digit code, once an organiser has approved them. No passwords to remember.
- **One contact at a time.** Each caller gets the next person on the list, taps to dial, and saves the outcome before moving on. There's a short 20-second break between calls, and a call left open is handed back after 30 minutes.
- **Your own questions.** Add written, multiple-choice or yes/no questions, and mark them as required or optional. Change them any time; earlier answers keep the wording they were given with.
- **Private by default.** Names, phone numbers, answers and notes are encrypted in the database. Names are shown to callers as an image and numbers open straight in the phone's dialler, which makes casual copying harder.
- **Names are optional.** If you only have a phone number for someone, that's fine. Callers will see "Name not known".
- **Safe on bad signal.** If a save fails and the caller presses save again, the call is only recorded once.
- **Results.** See progress at a glance and download everything as a CSV.

## Try it on your computer

You'll need Docker, and Python 3 for the setup script.

1. **Get an email account for sending codes.** Any provider with SMTP works. Make sure the sender address is verified. The app only sends over encrypted connections (STARTTLS on port 587, or `SMTP_SECURITY=ssl` on port 465).
2. **Run the setup script:** `python3 scripts/configure.py`. It asks for your email, the sender address and the SMTP server, then writes a `.env` file with fresh random passwords and an encryption key. It never overwrites an existing `.env`.
3. **Add your SMTP login** to `.env` (`SMTP_USERNAME` and `SMTP_PASSWORD`).
4. **Start it:** `docker compose up --build -d`
5. **Open it:** the organiser side is at **http://localhost:8004/login** and callers use **http://localhost:8003/pb/login**. Use `localhost`, because sign-in cookies need it.

Already running an older install under the name `open-calls`? Add `COMPOSE_PROJECT_NAME=open-calls` to `.env` so it keeps using your existing database.

Only the email in `ADMIN_EMAIL` gets organiser access to begin with, and only when the database is first created. After that, manage access from the organiser pages. **Keep the database volume and the `FERNET_KEY` safe**: without the key, the encrypted names, numbers and answers can't be read.

### Your first calls

1. Sign in on the organiser side and approve your callers' email addresses.
2. Open **Manage survey questions** and add a few questions.
3. Upload a CSV of people to call. It needs a `phone` column; `name` is optional. Try [the sample contacts](samples/contacts.example.csv), which are all made up. Files can be up to 5 MB and 10,000 contacts.
4. Your callers sign in and start calling.

If you change the questions while someone is mid-call, they're asked to reload before saving, and any answers that still fit are kept.

## Putting it online

The database stays inside Docker's private network, and both web ports only listen on your own machine. To put it on the internet you'll need:

- HTTPS, with **a separate address for the organiser and caller sides**, and `ALLOWED_HOSTS` set to exactly those names
- a reverse proxy in front, with `FORWARDED_ALLOW_IPS` set to its address so rate limits see real visitors
- ideally, something with passkeys or 2FA in front of the organiser side before you use real contact data

For a database on another machine, use `OPS_DB_SSLMODE=verify-full`. [SECURITY.md](SECURITY.md) walks through what the app protects and what's left to you.

## Running the tests

```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check app tests scripts
```

The quick checks run without a database. The rest need a **throwaway** PostgreSQL 17 server that's safe to wipe, because they create and delete databases and reset the `calls_admin` and `calls_caller` logins. Never point them at a real one.

```sh
export TEST_DATABASE_DSN='host=127.0.0.1 port=55439 user=postgres dbname=postgres'
.venv/bin/playwright install chromium
RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest -q
```

The browser test uses made-up contacts, never places a real call and sends no real emails. See the [testing notes](docs/verification.md) for what is and isn't covered.

## Licence

Resea Calls is free to use, change and share for any non-commercial purpose, under the [PolyForm Noncommercial licence](LICENSE). Charities, community groups, schools, public bodies and individuals are all welcome. You'll pay your own hosting, email and phone providers. Please keep the licence and the [notice](NOTICE) with any copies.

Because the licence rules out commercial use, it isn't "open source" in the strict OSI sense. The code is all public and you can do almost anything with it, as long as you're not making money from it. If you'd like to use it commercially, please [get in touch](COMMERCIAL.md) first.

## Supporting the project

Resea Calls is free and made by volunteers. If it's helped your group and you'd like to chip in, you can [buy us a coffee on Ko-fi](https://ko-fi.com/openresea). Every bit helps keep it going. Thank you!

## Dependencies

Dependencies are pinned with checksums in `requirements.txt`. To update them, edit `requirements.in`, then run `uv pip compile requirements.in --python-version 3.12 --generate-hashes --output-file requirements.txt`, check the result with `pip-audit`, and run the tests again.
