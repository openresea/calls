# Contributing

Use Python 3.12 or newer, PostgreSQL 17, and the development setup in README.md. Submit focused changes with a description of the user-visible behaviour and relevant verification. Keep the licence and required notice intact.

Use fictional contacts and reserved example email domains in fixtures, screenshots, and examples. Do not commit local `.env` files, real CSV imports, exports, database dumps, or cloud credentials. Keep organisation branding and mail-provider settings configurable.

Run Ruff and the unit suite for changes; run the disposable database and browser suites for authentication, authorisation, survey, allocation, or workflow changes. Tests use role-restricted database clients; do not replace these with owner credentials to make failures disappear. Never point TEST_DATABASE_DSN at a production or shared cluster.

Update `requirements.in` for runtime dependency changes, then regenerate the fully hashed lock with `uv pip compile requirements.in --python-version 3.12 --generate-hashes --output-file requirements.txt`. Audit the resulting lock and verify supported Python versions. Development tools are listed separately in `requirements-dev.txt`.

Preserve approval checks, exact-origin validation, shared rate limits, transaction locking, and append-only history. Explain migrations and recovery steps for schema changes; the SQL files initialise a fresh database and do not upgrade existing installations.

Before a public release, check that the security contact in SECURITY.md is monitored. Before deployment, test real SMTP delivery with a controlled address, review HTTPS/proxy configuration and backups, and complete a deployment-specific security review.

## Contributions and licensing

Bug reports and suggestions are welcome. Code contributions are paused until a contributor agreement is in place that permits both noncommercial distribution and separate commercial licensing. Do not submit third-party code without permission from its copyright holder.
