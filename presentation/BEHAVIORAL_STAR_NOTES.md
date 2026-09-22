# Behavioural Interview Notes (STAR)

Eight stories, bullet triggers only, so the delivery stays conversational. Keep **Action** the
longest part; end each with a one-line reflection.

> These are prompts and structure. Fill each with a real experience of your own; the worked
> example under story 5 comes from this project so you can see the shape.

Format: **S**ituation (2 lines) · **T**ask (1 line) · **A**ction (4–6 bullets) · **R**esult
(numbers if possible) · Reflection (1 line).

---

## 1. Serious technical problem

- S: system, scale, what was failing, who was affected
- T: what you owned
- A: how you narrowed it (metrics → logs → hypothesis → experiment), what you ruled out, the
  fix, how you verified
- R: measurable outcome
- Reflection: what you now instrument or test by default

## 2. Production incident

- S: alert, blast radius, time of day
- T: your role (IC, incident commander, comms)
- A: triage order, communication cadence, mitigation vs root cause, when you rolled back
- R: time to mitigate, time to resolve, follow-ups shipped
- Reflection: the runbook or alert that exists because of it

## 3. Disagreement or conflict

- S: the decision and the two positions
- T: reach a decision the team would own
- A: restated the other position accurately, found the shared goal, proposed an experiment or
  a reversible path, agreed criteria in advance
- R: what was decided, how it played out
- Reflection: disagree and commit; be explicit about which assumptions would change your mind

## 4. Requirements changed

- S: what changed, how late
- T: absorb it without derailing the release
- A: identified what the change actually invalidated (often less than feared), used existing
  boundaries, renegotiated scope explicitly, wrote down the new assumptions
- R: delivered date/quality
- Reflection: ports and adapters pay for themselves here

## 5. Mistake or failure

Worked example from this project:

- S: Building the upload handler, I inserted the file row and its audit row in one session and
  relied on the ORM to order them. The fast test suite ran on SQLite, which does not enforce
  foreign keys by default, so everything was green.
- T: Ship an integration suite against real PostgreSQL before calling the service done.
- A: Wrote the PostgreSQL fixtures to build the schema with the real Alembic migrations; the
  first end-to-end run failed with a foreign-key violation because the audit row was flushed
  first. Traced it to the absence of a mapped relationship; fixed it with an explicit flush
  after adding the file row rather than adding a relationship the domain did not need; added a
  16-way concurrent idempotent upload test while I was there; wrote the lesson into the testing
  doc and the review notes.
- R: The suite went green on both databases; the CI pipeline runs PostgreSQL for exactly this
  class of bug.
- Reflection: a test double must not be more permissive than production on the property you
  are claiming.

## 6. Process improvement

- S: friction the team lived with
- T: reduce it without a mandate
- A: measured it, proposed the smallest change, piloted it, made it the default only after it
  proved itself
- R: cycle time / defects / on-call load
- Reflection: automation beats reminders

## 7. Ambiguous project

- S: vague goal, many stakeholders
- T: turn it into something buildable
- A: wrote assumptions down first (as in `docs/ASSUMPTIONS.md`), got them challenged early,
  shipped a thin end-to-end slice, iterated on real feedback
- R: what shipped and what was cut deliberately
- Reflection: naming assumptions converts ambiguity into decisions others can veto

## 8. Severe deadline or time pressure

- S: the constraint
- T: what had to be true at the deadline
- A: ranked by risk, cut scope explicitly rather than quality silently, kept the tests and the
  deploy path, communicated what would not be there
- R: outcome
- Reflection: a smaller thing that works and is honest about its limits beats a larger thing
  that might

---

## Quick reminders

- Say "I" for your actions and "we" for team outcomes.
- Give one number per story.
- If asked "what would you do differently", have the answer ready; it is the reflection line.
