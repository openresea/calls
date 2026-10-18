# Testing notes

You'll need Python 3.12 or newer, with the runtime and development dependencies installed as described in the README.

```sh
python -m pytest -q
```

Without `TEST_DATABASE_DSN`, the database tests are skipped. The browser test also needs `RUN_BROWSER_TESTS=1` and Playwright's Chromium.

For the full run, use a throwaway PostgreSQL 17 server. The tests create and delete databases and change logins, so never use a shared or real one.

```sh
export TEST_DATABASE_DSN='host=127.0.0.1 port=55439 user=postgres dbname=postgres'
python -m playwright install chromium
RUN_BROWSER_TESTS=1 python -m pytest -q
```

## What's covered

Email codes (expiry, retry limits, single use under concurrency), same-origin and host checks, caller approval and expiry, handing out contacts and who can see them, the break between calls, encrypted answers, repeated saves of the same call, CSV import (including contacts without names and the sample file) and export, and the database permissions of each login.

The browser test signs in, edits and restores survey questions, and saves a call on a phone-sized screen. It also refreshes the screenshots, using made-up data.

## What isn't covered

The browser talks to the apps through test clients backed by a real PostgreSQL database, and emails go to a test inbox. The tests don't exercise a real email provider, a production proxy, HTTPS certificates, backup restores or your hosting setup, and they never place a real phone call.

## Dependency checks

```sh
pip-audit -r requirements.txt --disable-pip --no-deps
```

`requirements.txt` pins every dependency with a checksum. New advisories appear all the time, so check again before each release.
