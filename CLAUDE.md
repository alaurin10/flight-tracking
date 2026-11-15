# Instructions for Claude

## The plan

- This project is being built milestone by milestone from [`docs/PLAN.md`](docs/PLAN.md).
  Read it before starting work. Do one milestone per pull request, meet its acceptance list,
  and update its status table in the same pull request.
- When the plan and the code disagree about what exists, trust the code and add a line to the
  plan's decisions log.
- For anything the plan does not spell out, copy how
  [Snow Watch](https://github.com/alaurin10/Snow-Forecast) does it (same owner, same stack,
  same server).

## Pull requests

- **Always open a pull request** when you finish a piece of work: commit, push the branch, and
  open a PR against `main` without being asked. Then watch it (CI and reviews) until it merges.

## Rules that protect the project

- **Tests never touch the network.** Use `FakeSource`, `FakeTransport` and the saved pages in
  `backend/tests/fixtures/`.
- **Live requests to Google are rationed.** At most 10 in a session, at least 7 seconds apart,
  through `flighttrack doctor`. Stop at the first HTTP 429 or `/sorry/` page and do not retry.
  Never run `flighttrack calibrate` from a cloud session.
- **Never commit** `data/`, `out/`, `.env`, a database file, or real trip dates and
  destinations. The repository is public.
- **Price history is irreplaceable.** Migrations only add. The `observations` table is
  append-only; do not weaken its triggers.
