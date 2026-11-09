---
name: wiki-archivist
description: "Nightly habit that keeps a NyxWikiRecall wiki true: read one finished day's transcript, fix the wiki with cited edits, log the run, and report."
version: 1.0.0
author: Clotho (packaged with NyxWikiRecall)
license: MIT
metadata:
  hermes:
    tags: [memory, wiki, nyx-wiki-recall, curation, transcripts]
    related_skills: [llm-wiki]
---

# Wiki archivist: the nightly habit that keeps a recall wiki true

You installed NyxWikiRecall so your agent remembers better. The plugin is only half of that: it
can only recall what the wiki says, and a wiki drifts out of date every day. This skill is the
other half. Once a night, read the day that just ended, fix the wiki, write down what you did,
and report. Read it once, fill in the settings in step 1, and it can run unattended.

*Originally written by Clotho, an archivist agent, for the installation where NyxWikiRecall was
built. It follows the shape of the `llm-wiki` skill (orient, ingest, lint, log); if you have
`llm-wiki` installed, its advice applies here too.*

## When to use

- On a schedule, once a night, after the day's transcript is final (see step 9).
- When someone asks you to "update the wiki from yesterday" or "archive a day".
- When starting a brand-new wiki (read step 10 first).

Do not use it to answer questions from the wiki. The plugin and `wiki_read` do that.

## 0. Roles

- **The resident**: the agent whose memory the wiki is. Pages are written for the resident's
  recall, in the voice the schema asks for.
- **The operator**: the person who runs the installation. They set policy, answer open
  questions, and get the nightly report.
- **The archivist**: whoever runs this skill. It may be the resident itself, or another agent.
  This skill talks to the archivist.

One person and one agent may fill all three roles. Nothing below depends on who fills them.

## 1. Settings: work them out once, before the first run

Find each answer once and write it in the wiki's `SCHEMA.md` (or ask the operator once). Do not
guess again every night.

| Setting | What it is | Default |
|---|---|---|
| Wiki folder | The one wiki you maintain | The plugin's `wiki_path` setting: `plugins.entries.nyx-wiki-recall.settings.wiki_path` in Hermes's `config.yaml` |
| Schema | The wiki's own rules | `SCHEMA.md` in the wiki folder |
| Transcripts | Where the daily records are | `transcripts/` in `<Hermes home>/plugin-data/nyx-wiki-recall/` (usually `~/.hermes/plugin-data/nyx-wiki-recall/transcripts/`), unless `transcript.folder` in the plugin's `questions.txt` names another folder |
| Index | The list of pages | `index.md` in the wiki folder |
| Log | One entry per change and per run | `log.md` in the wiki folder |
| Report to | Where the nightly report goes | The cron job's delivery target (step 9) |
| Approval folders | Kinds of page that stay drafts until the resident says yes | Whatever the schema marks as personal; none if it is silent |

**The starter template is an example.** NyxWikiRecall ships `templates/starter-wiki/`. Copy it
to make your wiki; never write real entries into the plugin's own copy.

**The schema wins.** Where `SCHEMA.md` disagrees with this skill, follow `SCHEMA.md`. This skill
teaches the habit; the schema owns the house rules (layout, frontmatter, voice, size limits).

## 2. How the plugin sees the wiki (read this before editing anything)

You will edit pages for a reader that only ever sees short glimpses. Know how it reads:

- **Glimpses.** Each page's glimpse is the text between `<!-- glimpse -->` and
  `<!-- /glimpse -->`. A page with no markers uses its first paragraph. The decision model (JEV)
  reads only glimpses when it decides what fits a conversation.
- **Folder glimpses.** A folder's glimpse is in its `_about.md`, between the same markers.
  A folder with no glimpse is never offered, and nothing inside it is either.
- **Top folders steer everything.** For each message the plugin asks, in order: which top
  folders fit; then which sub-folders and pages one level down fit; then which pages inside a
  chosen sub-folder fit. A page is reached only through its top folder's glimpse. A page deeper
  than top folder, sub-folder, page is never offered.
- **Hidden things.** With `skip.hidden = yes` (the default in `questions.txt`), any folder or
  file whose name starts with `_` or `.` is never read. A `skip.dir = <name>` line hides a folder
  by name. Markdown files in the wiki's top folder (`SCHEMA.md`, `index.md`, `log.md`) are guides,
  never pages.
- **Keep drafts and sources hidden.** Drafts go in `_drafts/`, copied transcripts in `_sources/`
  (or folders listed in `skip.dir`). If a drafts folder is visible, the resident is shown
  unapproved drafts as if they were true. If a sources folder is visible, raw transcripts compete
  with real pages. Keep `index.md` and `log.md` in the top folder or in a `_` folder.

## 3. Reading a transcript

NyxWikiRecall writes transcripts by itself; nothing else needs to be installed. There is one file
per local day, named like `Mar-04-2026-transcript.md`, and `transcriptIndex.md` lists the days.

**The header tells you whether the day is finished:**

```
# March 4, 2026

**Status:** final
**Messages:** 12
...
```

- `**Status:** open`: the day is still being written. Do not archive it.
- `**Status:** final`: the plugin wrote the day for the last time, after `finalize.after`
  (default 02:00) the next morning. A final file is never written again.
- If Hermes was off at that time, the plugin finishes missed days at its next check, oldest
  first (up to `finalize.catchup.days`, default 7).
- A day with no conversation has no file at all. That is not an error.

**Each message looks like this:**

```
## Telegram / Family chat

### Sam (2026-03-04 18:20:11)

How much compost do the raised beds need this spring?

Wiki pages:
1. topics/compost.md (0.91)
2. projects/raised-beds.md (0.74)

### Assistant (2026-03-04 18:20:19)

About two bags per bed...
  [wiki_read: topics/compost.md]
```

- `## Platform / chat name` starts a stretch of conversation.
- `### Speaker (time)` is one message. The speaker is the sender's display name, or the
  plugin's `transcript.user.name` (default `User`) when there is none. The agent's replies use
  `transcript.assistant.name` (default `Assistant`). Lines in brackets under a reply are tool
  calls.
- `### Heartbeat (time)` is not a person. It is one of the agent's own scheduled turns: a
  recurring instruction someone set up with Hermes's `/heartbeat`, which fires when the chat is
  idle. `### Background process (time)` is Hermes reporting on a job it ran. Neither is someone
  talking to the agent, but the replies to them can contain real work worth recording.

## 4. The `Wiki pages:` block: what the resident was given

Under a message the plugin looked at, a `Wiki pages:` block says what happened:

| Block says | Meaning | What it tells you |
|---|---|---|
| numbered lines, `path (score)` | These pages were shown, highest score first | Your edit map: check each page against the message |
| `none` | JEV scored the wiki and nothing reached `threshold` | Either the wiki has no page for this, or a page exists and its glimpse missed |
| `ineligible` | A skip rule matched (a heartbeat, a slash command, a short "thanks"), so nothing was looked up | No signal at all. Not a miss |
| `not scored` | JEV failed or timed out | No signal. Treat it exactly like a message with no block. Not a miss |
| no block | The plugin did not look (for example, no wiki was set yet) | No signal |

What the resident actually saw for each listed page, inside the reply's context:

```
### Compost

Two bags of compost per raised bed each spring, worked in before planting.
(2026-02-11, src: _sources/transcripts/Feb-11-2026-transcript.md#L88)

Match confidence: 0.91
File: topics/compost.md
Short name: compost
```

So the resident saw the title, the glimpse and the score, never the full page, unless it
called `wiki_read` (you will see `[wiki_read: ...]` under its reply).

Things to keep in mind:

- **The cap.** At most `max.pages` pages are shown per message (default 8), highest score first.
  Eight listed pages means a relevant page may have been cut by the cap. That is not a glimpse
  problem.
- **Already shown.** A listed page whose glimpse was already in the conversation from an earlier
  message is not repeated; the resident gets a one-line reminder instead.
- **A block is about what the resident was given, not what the message is worth.** Messages
  marked `ineligible`, `not scored`, or with no block can hold the most important facts of the
  day. Mine every message.

**Reading `none`.** Two different problems, opposite fixes:

- *No page exists for the subject*: a candidate for a new page (step 6, "Create").
- *A page exists but was not picked*: do not make a second page. Reword that page's glimpse, or
  its top folder's glimpse, so it matches how people actually talk about the subject (step 7).
  Twin pages for one subject corrupt recall.

Before creating any page, search the wiki for the subject. Most "missing" pages already exist
under a neighbouring name.

## 5. Principles

1. **Keep the story; never quietly swap it.** A record holds what was said, in order, mistakes
   and corrections included. Edit the wiki to match what happened, never to make it tidier. If
   something does not fit (a claim with no source, a contradiction, a name that could be two
   people), stop and look; do not reason from it and do not smooth it over.
2. **The raw record is the source of truth.** Summaries (context-compaction summaries, digests,
   anyone's notes) are never copied into the wiki without a record behind them. If you cannot
   cite it, leave it out and say so in the report.
3. **Helpers propose; the archivist decides.** A script, a small model, or the plugin's own
   suggestions may point at candidate facts. You decide the wording and whether a page should
   exist.
4. **Small, cited edits.** Every change traces to a file and line. If you cannot point at the
   source, you are guessing: park it as an open question instead.
5. **Credentials never belong in the wiki.** If you find a password, key or token on a page,
   replace it with `[redacted]` and tell the operator which file.
6. **Archiving is local.** A run never publishes or shares anything outside the installation.

## 6. The nightly run

One run covers one finished day. If several finished days are waiting, do the oldest first, one
day per run, so each run stays small.

### 6.1 Claim the day

Two archivists must never work on the same day at once. In the wiki's top folder, look for
`.archivist.lock`.

- If it exists and is less than 6 hours old, stop: someone else is working. Report it.
- If it is older than 6 hours, a run died. Note it in the report, then replace it.
- Otherwise create it with one line: the day, who you are, and the start time, such as
  `2026-03-04 | resident | started 2026-03-05 02:31`.

Also check `log.md`: if the day already has an `archived` entry, stop. It is done.

### 6.2 Make a restore point

Before editing anything:

- If the wiki is a git repository, commit everything: `git add -A && git commit -m "before archiving 2026-03-04"`.
- If it is not, copy the wiki folder (you may leave out `_sources/`) to a dated backup outside
  the wiki, such as `<wiki folder>-backups/2026-03-04/`.

Write the commit id or backup path down; it goes in the log.

### 6.3 Orient

Read `SCHEMA.md`, then `index.md`, then the last 20 to 30 entries of `log.md`. Note how recent
runs were written up, so yours matches.

### 6.4 Load the day

- Open that day's transcript and check the header says `**Status:** final`. If it says `open`,
  stop, release the lock, and try again later.
- If there is no file for the day, nothing was said. Log `no transcript` and stop.
- Copy the file, unchanged, into `_sources/transcripts/`. Do not edit the copy: citations point
  at its line numbers.
- Read that one day only.

### 6.5 Pull out the facts

List every new, corrected, or contradicted fact, each with its source line. Go through every
message, including heartbeats, background-process replies, and messages with no `Wiki pages:`
block (step 4).

### 6.6 Edit, create, or park

- **Edit** where the record clearly supports the change.
- **When the record and the wiki disagree, that is an edit, not a question.** The wiki is wrong;
  fix it. Ask a question only about what the record itself leaves undecided: who said or wrote
  something, how much personal detail belongs on a page, a choice that belongs to the operator
  or the resident.
- **Bookkeeping you can check in the wiki is just fixed.** A stale count in `index.md` is
  recounted name by name and corrected in the same run.
- **Contradictions** get a procedure, not a coin flip: compare dates (newer usually wins). If
  the claims truly conflict, keep both on the page with their dates and sources, mark the page
  the way the schema says (for example `contested: true` in the frontmatter), and flag it in the
  report.
- **Create** a page when a subject comes up in two or more sources, or is central to one; never
  for a passing mention. (In a new wiki, relax this: see step 10.) Give it the frontmatter the
  schema asks for, a glimpse with a citation, at least two links to other pages once the wiki
  has enough pages, and add it to `index.md`.
- **Approval folders.** A page in a kind the operator listed as needing approval is written into
  `_drafts/` at the path it will have (`_drafts/people/sam-rivera.md`) and waits for the
  resident's yes. Drafts are hidden from recall, so nothing unapproved reaches the resident.
- **Size.** Follow the schema. If it is silent: split a page well past about 3,000 characters
  that covers more than one subject (the parent keeps its glimpse and a list of parts; back up
  the page first). A single long story stays whole. A sub-folder needs more than one page.

**Citation format.** Every fact you add ends with the date of the record and its file and line:

```
(2026-03-04, src: _sources/transcripts/Mar-04-2026-transcript.md#L123)
```

### 6.7 Check your work

- Spot-check two citations you wrote tonight: open the cited file at that line and confirm it
  says what the page claims.
- Scan every page you touched for credentials: anything that looks like a password, an API key
  (for example text starting `sk-`), a token, a private URL with a key in it, or a long random
  string. Replace it with `[redacted]`.
- Check every page you created or moved is in `index.md`, every new folder has an `_about.md`
  with a glimpse, and nothing meant to be private sits in a visible folder.

### 6.8 Record the run

Append one entry to `log.md` in its usual format:

```
## [2026-03-04] archived | Mar-04-2026-transcript.md
- Source: _sources/transcripts/Mar-04-2026-transcript.md (final, 12 messages)
- Changed: edited topics/compost.md; created people/sam-rivera.md; drafted _drafts/people/ana.md
- Open: is "Ana" in the 18:40 message the same Ana as people/ana-lopez.md?
- Restore point: commit 3f2a9c1
```

Then delete `.archivist.lock`.

### 6.9 Report

Your final reply is the report, and the cron job delivers it to the operator. Keep it short and
put everything that needs a decision in the message itself, not only in a file:

- pages edited, created, and drafted;
- drafts waiting for approval;
- open questions;
- misses by kind: new-page candidates, and glimpses you repaired;
- anything odd (an `open` day, a stale lock, a missing transcript).

**Finish what you start.** Once a run begins, complete it, or roll back to the restore point.
A half-updated wiki is worse than a skipped night.

### Restoring

If a run went wrong:

- git: `git log` to find the "before archiving" commit, then `git reset --hard <commit>` (or
  `git revert` the archiving commits if you want to keep history).
- backup copy: replace the wiki folder's contents with the dated backup.

Then delete the day's `archived` entry from `log.md` (or add a `rolled back` entry), remove
`.archivist.lock` if it is still there, and run the day again.

## 7. Glimpse craft

Every page's glimpse is what JEV reads to decide whether to show the page, and what the resident
sees when it does. Glimpse quality is recall quality, and glimpse length is context cost.

- **Stands alone:** it makes sense with no other page in view. Name the person or thing; never
  rely on "she" or "this".
- **Tells neighbours apart:** the fact that separates this page from similar ones belongs in the
  glimpse, not only in the body.
- **Cited:** it ends with a source in the citation format above.
- **Short:** two to four sentences. When the page grows, the glimpse does not; that is what
  splitting is for.
- **Repair before duplicating:** when a page exists but was missed (`none`), reword its glimpse
  first. If a whole area keeps being missed, the top folder's `_about.md` glimpse is the likely
  culprit, because nothing under a folder is reached until the folder matches.

## 8. The architecture pass (floor plan, not furniture)

Nightly runs maintain content. Every few weeks, once real friction has shown up, step back and
judge the structure. This pass **reports first**: findings and proposed changes, with nothing
restructured until the operator (and the resident) agree. Never move the walls while someone
lives in the house.

Check:

1. Do the top folders still match how people actually talk to the resident?
2. Twin pages that should merge.
3. Orphan pages that nothing links to.
4. Broken links.
5. `index.md` against the real folders, counted name by name.
6. Frontmatter against the schema.
7. Stale pages: not updated in 90 or more days while newer sources mention the subject.
8. Contested pages still unresolved.
9. Oversized pages and long glimpses.
10. Folders or pages without a glimpse (they are invisible to recall).
11. Page names against the words people actually use.
12. `log.md` past about 500 entries: move older entries into a `_log-archive/` file.

## 9. Scheduling it nightly with Hermes cron

Hermes's own scheduler runs this skill as a cron job. Pick a time after the plugin finishes the
day: `finalize.after` (default 02:00) plus up to `finalize.check.minutes` (default 10). 02:30 is
a safe default. Cron times use the timezone set in Hermes's config, or the computer's clock when
none is set; the plugin finishes days by the computer's clock, so keep the two the same.

From a terminal:

```bash
hermes cron create "30 2 * * *" \
  "Run the wiki archivist for the oldest finished day that log.md does not mark archived. Follow the skill step by step and end with the report." \
  --skill nyx-wiki-recall:wiki-archivist \
  --name "Wiki archivist" \
  --deliver telegram:<your chat id>
```

- `nyx-wiki-recall:wiki-archivist` is this skill's name while the plugin is enabled (plugin
  skills are named `plugin:skill`). If you copied the skill into your own skills folder instead,
  use `--skill wiki-archivist`.
- `--deliver` sends the report to a chat. Any target Hermes supports works (`origin` from a chat,
  `discord:<channel id>`, and so on). Without it, a job made from the terminal saves its output
  under `~/.hermes/cron/output/`.
- Or ask your agent in chat: "Every night at 02:30, run the wiki-archivist skill and send me the
  report." It uses the same scheduler.
- Check it with `hermes cron list`, run it once now with `hermes cron run <job id>`, and see past
  runs with `hermes cron runs <job id>`.

The archivist's own cron turns are not written into the transcripts: the plugin leaves out the
`cron` platform by default (`transcript.skip.platforms`).

## 10. Day one: starting from an empty wiki

- Copy `templates/starter-wiki/` from the plugin to where you want the wiki, and point
  `wiki_path` at the copy.
- For the first days, expect `none` on almost every message. The wiki is empty, so nothing can
  match. That is not a failure, and every `none` is a new-page candidate.
- Relax the "two or more sources" rule: early on, create a page when a subject is central to one
  day. Most of your first pages will cite a single transcript. Add sources as later days mention
  the subject.
- Do not require two outbound links until the wiki has a couple of dozen pages.
- Write the top folders' glimpses with care first. They decide whether anything below them is
  ever reached.
- Change the top folders if the starter ones do not fit. Four or five distinct folders beat
  twelve overlapping ones.

## 11. Keeping costs down

- **Read one day per run.** Never load a week of transcripts at once.
- **Few top folders, short glimpses.** Every message the plugin scores sends all top-folder
  glimpses to JEV, then whatever one level down belongs to the folders that matched. Fewer,
  shorter, more distinct glimpses mean smaller requests and better matches.
- **Use the `Wiki pages:` hits as your edit map.** Start from the pages listed under each message
  instead of rereading the whole wiki. Read `index.md`, not every page.
- **Tune `questions.txt`:**
  - `threshold` (default 0.5): higher shows fewer, surer pages.
  - `max.pages` (default 8): fewer pages per message means less context for the resident.
  - `budget.seconds` (default 20): how long a lookup may take before it gives up (`not scored`).
  - `skip.line`, `skip.short`, `skip.dir`: skip messages or folders that never need the wiki.
- **Run it once a night,** not every hour. A day is not final before `finalize.after` anyway.

## 12. Traps

- **Same name is not the same person.** Two people, or a person and a character, can share a
  name. Before merging or editing, check which one the source means, and say which on the pages.
  A silent merge is the most expensive mistake an archivist can make.
- **Sensitive material.** For letters, confessions, hard nights: check who wrote it from the
  source itself, and record it faithfully, sad or unflattering parts included. What the source
  leaves open goes to the operator as a question.
- **A summary is not a source** (principle 2), however confident it sounds.
- **A missing block is not a missing memory** (step 4). What a message was given and what it is
  worth are different questions.
- **`not scored` and `ineligible` are not misses.** Do not reword glimpses because of them.

## 13. What "done" looks like

A nightly run is done when: the day was final and is copied into `_sources/`; every message was
read (hits, both kinds of `none`, and messages with no signal); every clear edit is made with a
citation; bookkeeping is fixed, not parked; open questions are only what the record leaves
undecided; drafts are in `_drafts/`; two citations were spot-checked and touched pages were
scanned for credentials; the run is in `log.md` with its restore point; the lock is gone; and
the report is in the message, where the operator will see it.
