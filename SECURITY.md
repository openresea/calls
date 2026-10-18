# Security and privacy

Resea Calls holds people's names, phone numbers and what they told your callers, so it's worth taking a little care when you set it up. This page explains how the app protects that data and what's left for you to do. Our automated tests are useful evidence, but they aren't an independent security audit or penetration test.

## What the app stores about people

- **Contacts.** Name (optional) and phone number, encrypted. Each call's outcome, survey answers and notes, also encrypted. When and how often each contact was called, and by whom, is stored in plain form.
- **Callers.** Their email address, who approved them, and which calls they made.
- **Organisers.** Their email address, and who changed which survey question.
- **Sign-in data.** Email addresses waiting for a code, and sign-in sessions. Rate limits are stored under a keyed hash, so no IP addresses are kept in the database. Expired sign-in records are cleaned up each time the app starts.

Results are linked to the contact, so they are **not anonymous**. The app doesn't write web access logs, so callers' IP addresses aren't recorded by default. Your reverse proxy may log them, so check its settings.

There's no automatic deletion. Decide how long you need the data, then remove it on a schedule using a database admin login.

## Encryption

Names, numbers, notes and answers are encrypted with `FERNET_KEY`. This protects you if someone gets hold of the database or a backup on its own. It doesn't protect against someone taking over the running app, because the app needs the key to work.

- Keep `FERNET_KEY` safe and backed up. **If you lose it, the encrypted data is gone for good.** Changing it without re-encrypting everything has the same effect.
- Duplicate-save checks use a keyed hash, so short answers like "Yes" can't be guessed from the database.

## Signing in

Callers and organisers sign in with an emailed code, but only after an organiser has approved them. There's no public sign-up. Approval is checked on every request, so removing someone, or letting their access expire, signs them out straight away.

Email codes prove that someone can read that inbox. That's convenient, but it isn't two-factor authentication. Anyone who gets into an organiser's email can sign in as them. For sensitive contact lists, put the organiser side behind something with passkeys or 2FA, and ask your team to protect their email accounts with 2FA too. OWASP explains [email's limits](https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html) and [one-time code handling](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).

How codes work:

- Codes are random, last ten minutes and work once. Asking for a new code cancels the old one.
- Five wrong guesses use up a code.
- Only a keyed hash of each code is stored, using `EMAIL_CODE_SECRET`.
- The response is the same whether or not an email is approved, so nobody can work out who your callers are.
- Codes and email passwords never appear in logs.

Limits are shared across all copies of the app: three emails per address every 15 minutes (at least a minute apart), 20 per IP address every 15 minutes, 200 per app per hour, and 60 sign-in attempts per IP address every 15 minutes. Callers behind the same office or mobile network share an IP limit. Someone could deliberately use up the shared limits, so put some abuse protection in front as well.

Sessions last twelve hours and use `Secure`, `HttpOnly`, `SameSite=Strict` cookies. Cookies aren't separated by port, so run the organiser and caller sides on separate addresses.

## What callers can see

Names are shown as an image and dialling goes through a redirect to `tel:`, so the number never appears in the page. That discourages casual copying but can't stop it: a caller can screenshot the name, record the call, or find the number in their phone's history. Calls are made from the caller's own phone, so the person they ring may see the caller's number, and their phone provider may charge them. This isn't a number-masking or VoIP service.

Name images also mean screen readers can't read contact names. The rest of the call page is fully labelled.

## How the parts are kept apart

The organiser and caller apps log in to the database with different, limited accounts. The caller account can't read organiser sessions, change the survey or access, or read saved results back. Which contact each caller can see is enforced by the app, not by the database, so a compromised caller app could reach the encrypted contacts and the key it holds.

Each call belongs to one sign-in session. Database locks make sure two callers never get the same contact, that a call is only saved once, and that signing out doesn't skip the break between calls. Results and survey history are add-only for the apps. A database admin could still change them, so they aren't tamper-proof.

## Putting it online safely

- Use HTTPS everywhere except on your own computer.
- Give the organiser and caller sides separate addresses, and set `ALLOWED_HOSTS` to exactly those names.
- Set `FORWARDED_ALLOW_IPS` to your reverse proxy's address, never `*`, and make sure the proxy passes the original scheme (`https`). Otherwise form submissions are rejected as cross-site.
- Keep the database private. If it's on another machine, use `OPS_DB_SSLMODE=verify-full`.
- Back up the database and keys, store them securely, and test that you can restore them.

What the app already does: every change must come from the app's own pages (matching `Origin` or `Referer`); host names are checked; request sizes are capped; scripts are locked down by a Content Security Policy; pages can't be embedded in other sites; private pages aren't cached; CSV exports neutralise spreadsheet formulas; and no tracking scripts are loaded.

## Housekeeping

- Keep `.env`, database exports, real CSVs, email passwords and keys out of git and out of public issues.
- Changing `EMAIL_CODE_SECRET` cancels pending codes and changes duplicate-save checks, so do it when nobody is mid-call. Sign everyone out separately if you're responding to an incident.
- Check dependencies regularly with `pip-audit` and keep them up to date.
- Before importing real contacts, test real email delivery and your HTTPS and backup setup.

## Reporting a problem

If you find a security issue, please email [resea@tuta.io](mailto:resea@tuta.io) privately rather than opening a public issue. Let us know the version, the steps to reproduce it using made-up data, and what you think the impact is. Please don't include real contact data, passwords or sign-in codes.

If the problem affects a particular installation, please tell whoever runs it too.
