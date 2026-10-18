# Contributing

Thanks for your interest in Resea Calls!

Bug reports and ideas are very welcome. Please open an issue, and use made-up contacts and example email addresses in anything you share.

We're not able to accept code contributions just yet. We first need a contributor agreement that lets us keep the project free for non-commercial use while also offering separate commercial licences. Please don't send code written by someone else without their permission.

If you're working on your own copy:

- Use Python 3.12 or newer and PostgreSQL 17, set up as described in the README.
- Never commit `.env` files, real contact lists, exports, database dumps or credentials.
- Run Ruff and the quick tests. If you touch sign-in, permissions, questions, call allocation or saving calls, run the database and browser tests too, against a throwaway database (never a real or shared one).
- The tests use the same limited database logins as the real app. Please don't switch them to an admin login to make a failure go away.
- Keep approval checks, same-origin checks, shared rate limits, database locks and add-only history intact.
- `app/sql/schema.sql` sets up a new database; it doesn't upgrade an existing one. If you change it, explain how existing installs should be updated.
- To change a dependency, edit `requirements.in` and regenerate `requirements.txt` with `uv pip compile requirements.in --python-version 3.12 --generate-hashes --output-file requirements.txt`, then run `pip-audit`.
- Keep the licence and notice files intact.
