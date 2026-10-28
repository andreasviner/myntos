# Feedback from anybody, kept without a name

Anybody can tell Myntos that something is wrong or missing: a person on
`/tilbakemelding/`, signed in or not, an AI agent through
`/api/v1/tilbakemeldinger` with or without a key, a developer with curl. The
operator reads every item by hand. Built 2026-10-21 (migration
`a84dfde214d1`), because the owner wanted humans and agents alike to help
build the site, anonymously.

Read this before touching `domain/feedback/`, `api/v1/feedback.py`, the page,
or the personvernerklæring's section e3. Everything below is a promise the page
and the privacy statement make, and each holds only as long as the code it
names stays the shape it is.

## Anonymous by construction

"We do not store who you are" is kept by four things, and losing any one of
them breaks it:

1. **The table has nowhere to put a name.** `feedback` has no `user_id`, no
   `company_id`, no IP address, no user agent and no foreign key at all.
   `test_the_table_has_no_column_that_could_name_the_sender` lists the
   columns exactly, so adding one fails a test that sends you here.
2. **The routes do not ask who is calling.** No `CurrentUser`,
   `CurrentCaller`, `CurrentCompany` or `OptionalSession` in
   `api/v1/feedback.py`. A signed-in person's browser sends the session cookie
   like on every request, and an agent may send its bearer token; nothing
   reads either. A garbage token is a 201 here
   (`test_a_signed_in_person_and_a_key_are_not_read`), which is the proof.
3. **The request leaves no line in the journal.** uvicorn logs every request
   with the client's address, which is the sender's own because Caddy forwards
   it (`X-Forwarded-For`); the journal would then hold "this IP sent feedback
   at 14:02", next to the row created at 14:02. `keep_out_of_the_access_log`
   drops uvicorn's access line for every path under `/api/v1/tilbakemeldinger`,
   installed by `create_app`. Caddy itself writes no access log for
   `api.myntos.no` (checked 2026-10-21: no `log` directive). **Turning on
   Caddy's access log, or putting the API behind a proxy that logs, breaks
   this**, and the personvernerklæring with it.
4. **The operator's notice carries no text.** `FEEDBACK_NOTIFY_TO` gets one
   mail per quiet hour saying a number and a kind arrived, never the words,
   because mail leaves through Cloudflare without an EEA promise
   (ARCHITECTURE.md, "What the mail provider sees") and people write what is
   on their screen, which in an accounting program is somebody's books.

What anonymity costs, said on the page because the person pays it: **nobody
can find an item again for its sender.** The answer to a send carries a
**reference**, 120 random bits, shown once and stored only as a SHA-256 digest
(it deletes a row, so it is a credential). It is the only key to the item:
`GET …/{reference}` for the status and the reply, `DELETE …/{reference}` to
withdraw it. The answer's `url` is `/tilbakemelding/#ref=…`, in the fragment
like a mailed link, so the reference reaches no server log, the static site's
included.

## The sender's words are never published

The operator may put an item on the public list (`GET /api/v1/tilbakemeldinger`,
the bottom of the page), and doing so **is** writing a one-line `summary` of
it. The list shows the summary, the status and the operator's reply; never the
sender's text. The text is the one part nobody but the sender has read for what
it might contain, and a regnskapsfører describing a bug describes a client's
books. Most items are never published; the list is for "is this known?", not a
complete tracker.

## The cap, and why it counts everybody together

The senders are anonymous, so there is nobody to count one at a time, and a
limit per source address is not the application's to keep
(ARCHITECTURE.md § Security model: no source-based rate limiting in the
application; it would belong in Caddy). Two caps on the table instead, both
counted from rows:

- `FEEDBACK_MAX_PER_HOUR` (60): everything that arrived in a rolling hour.
  429 `feedback_busy`, `Retry-After: 3600`.
- `FEEDBACK_MAX_UNREAD` (500): items still `new`. 503 `feedback_inbox_full`,
  `Retry-After: 86400`. A table nobody reads is not worth filling, and this is
  what stops a flood from growing the database without bound.

Both are a denial of the feedback box, not of Myntos: a flood fills the hour
and legitimate senders wait. That is the accepted cost of not knowing who
anybody is. If it happens, a rate limit per address in Caddy is the next step,
and it must not log what it limits.

Text is 10 to 5000 characters after trimming; control characters other than
newline and tab are removed, because the operator reads it in a terminal. A
`page` and an `agent_name` are one line each. `source` (`person` or `agent`)
and `agent_name` are taken on trust and decide nothing; a person's submission
never keeps a program name.

## The operator reads on a page and acts in the shell

Reading and the status are on `/drift/` (docs/operator.md), opened by the
deployment's operator key and nothing else: every item with the sender's text,
newest first, and a status to set. Everything that reaches somebody or cannot
be undone stays in the shell:

```
cd backend
uv run python -m myntos.domain.feedback.triage list                 # unread
uv run python -m myntos.domain.feedback.triage list --status all
uv run python -m myntos.domain.feedback.triage show 12              # marks it read
uv run python -m myntos.domain.feedback.triage reply 12 "Rettet i dag."
uv run python -m myntos.domain.feedback.triage status 12 planned    # read, planned, done, declined
uv run python -m myntos.domain.feedback.triage publish 12 "Lagre virket ikke på mobil"
uv run python -m myntos.domain.feedback.triage unpublish 12
uv run python -m myntos.domain.feedback.triage delete 12            # spam, or a sender's request
```

Until 2026-10-28 there was no page at all, and this section said why: a route
that reads every stranger's words would be the most attractive one in Myntos,
guarded by whatever an operator role turned out to be. The owner asked for
the page anyway, after a customer's report had to be answered from the
journal over ssh, and the objection is met rather than dropped. It is not a
role: there is no operator account, membership or session, only one 256-bit
key per deployment, stored as its digest, that no company's key or login can
stand in for. Unset, the routes answer 404. And it is not the whole of the
shell's power: a reply reaches the sender, a summary reaches the public list
and a deletion is for good, so a key that leaks can mark items read and
nothing worse (`test_a_leaked_key_can_change_a_status_and_nothing_else`). On
the production box the command reads `<repo>/.env`, so it is production's
feedback, as the page is.

Wherever the text is shown, it was written by a stranger to be read by
whoever reads it. `show` prints it between markers labelled untrusted, because
if the operator's terminal has an assistant in it, that is who reads it
(docs/agents-and-api.md rule 7, from the other side). The page puts it in with
`textContent` only, and the same goes for an assistant in the operator's
browser.

## Retention

Done and declined items are deleted `FEEDBACK_RETENTION_DAYS` (365) after
they were closed, by the daily job in `main.py` that also purges deleted
companies. Open items are kept until closed, because somebody is waiting for
them. A sender's withdrawal deletes at once. Nothing points at `app_user`, so
account erasure (docs/gdpr.md) has nothing to do here, and the reference is
the sender's whole right to erasure; a sender who lost it writes to
kontakt@myntos.no, and the operator deletes the item once it is found.

## For agents

Every route is `NO_KEY_NEEDED` and taught by the skill `myntos-tilbakemelding`;
the help area `feedback/` explains the page. `llms.txt` tells an agent working
in the books to report what does not match its documentation, saying route,
status and `code` and never anything from the books, and to tell its person
in one sentence before it sends. An AI with no HTTP requests (a chat
assistant helping somebody) writes the report into a link instead:
`/tilbakemelding/#type=feil&tekst=…&side=…` opens the form filled in, and the
person presses the button. `lib/feedback.ts` reads and builds that fragment.

## Gaps

- **No dedup.** Two reports of the same bug are two rows; the operator merges
  them by publishing one and declining the other.
- **No way to answer a sender who lost the reference**, by design.
- **The caps live in the database, not at the edge.** A determined flood
  costs the hour's feedback; see the cap above.
- **No attachments.** A screenshot would be the most useful thing a person
  could send and the most likely to carry somebody's books, so not without a
  way to strip them that nobody has designed.
