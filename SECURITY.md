# Security model

This app stores contact details and survey answers. It has not had an independent security audit or penetration test. Operators need to review hosting, data handling, and access controls for their deployment.

## Authentication and authorisation

Any ordinary email provider can receive a code, but an administrator must first approve the address. There is no public registration. Administrator and caller approval are checked on each protected request; revocation and caller expiry invalidate existing access. Sessions expire after 12 hours and use Secure, HttpOnly, SameSite=Strict cookies. Deploy the two apps on separate HTTPS hostnames, because cookies are not isolated by port.

Codes are cryptographically random, single-use, and valid for 10 minutes. A database row lock serializes verification. Five failed attempts consume a challenge, including when verification fails. Resending invalidates older challenges. Stored codes use HMAC-SHA256 with a separate secret, rather than a brute-forceable unkeyed six-digit-code hash. Codes and SMTP credentials are never deliberately logged. SMTP certificate verification is enabled for STARTTLS and SSL.

Database-backed limits apply across application workers: three sends per email per 15 minutes, a 60-second resend interval, 20 requests per source IP per 15 minutes, 200 requests per service per hour, and 60 verification attempts per IP per 15 minutes. Eligibility responses are generic. Configure the proxy correctly for client IPs; otherwise users behind a proxy share one limit. An attacker can exhaust shared limits, so upstream abuse protection is still needed.

Email possession is the only built-in authentication factor. A compromised mailbox or phishing attack can compromise that account. For sensitive contact lists, protect administration with an independently enforced passkey/MFA access gateway and require MFA on authorised mailboxes. OWASP explains [email's limitations](https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html) and [OTP handling requirements](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).

## Data and service boundaries

Fernet encrypts contact names, numbers, notes, and answer snapshots. Operational metadata (emails, outcomes, timestamps, assignment relationships, survey wording and audit records) remains readable in the database. Idempotency fingerprints use keyed HMAC to avoid exposing low-entropy answers through an unkeyed hash. Both services need the encryption key. This protects database-only theft; compromise of an application runtime can expose data it can access.

Admin and caller have different restricted PostgreSQL roles. The caller role cannot read admin sessions, modify survey definitions or administrator access, or read full saved results. Caller contact and assignment access is limited by the HTTP application's ownership checks, not PostgreSQL row-level security. A caller service compromise can access encrypted contacts and its runtime decryption key; do not describe this as protection against server compromise.

An assignment belongs to one session. Per-caller transaction locks and unique constraints prevent concurrent contact allocation. A cooldown is derived from assignment history, so signing out does not reset it. Submission locks and a unique assignment constraint prevent duplicate results. Historical responses retain their original survey and encrypted notes.

Results and survey audit entries are append-only for application roles. A database owner can bypass those controls; they are not cryptographically tamper-proof. Retention and legitimate deletion require an operator-controlled database process and backups policy; there is no automated retention or erasure workflow yet. Do not use unlimited retention by default.

## Browser controls and limits

Unsafe requests require the exact same origin (including scheme). Host allowlists, request size limits, output escaping, CSV formula neutralisation, nonce-based script CSP, frame restrictions, and no-store responses are enabled. Inline styles are allowed for the existing interface. No external tracking scripts are loaded.

Names appear as PNG images and dialing uses a server-side redirect to `tel:`. A caller can capture the image or redirect, use OCR, record the call, and see the number in their phone history. Native dialing can reveal the caller's own telephone number and incur their provider's charges. This app is not a number-masking service or a VoIP provider. Name images also limit screen-reader access to contact names; the survey controls remain labelled.

## Operations

Keep `.env`, database exports, real CSVs, SMTP credentials, and encryption keys out of public repositories. Store backups and keys securely and test restoration. Losing `FERNET_KEY` makes encrypted records unreadable; changing it without re-encryption also does. Rotating `EMAIL_CODE_SECRET` invalidates pending codes and changes submission fingerprints, so coordinate rotation when callers are idle. Existing session tokens must be separately revoked during incident response.

Use HTTPS, restrict admin access, retain security monitoring without codes or contact data, patch dependencies, and audit the deployment before importing real contacts. Authentication records are cleaned on service startup; arrange periodic service maintenance for long-running deployments. Real email delivery, cloud deployment, disaster recovery, load, and penetration testing are not part of the local automated checks.

## Reporting

Report vulnerabilities privately to [resea@tuta.io](mailto:resea@tuta.io). Include the affected version, reproduction steps using fictional data, and the expected impact. Never include live contact data, credentials, or sign-in codes. For an incident affecting a particular installation, also contact its deployment owner.
