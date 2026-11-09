# NyxWikiRecall

NyxWikiRecall attaches short wiki glimpses to a Hermes turn. A decision model picks the glimpses. The agent calls `wiki_read` when it wants the whole article.

It also writes a daily Markdown transcript of every conversation, by itself, and finishes each day after midnight. Install it and it keeps the transcripts that a wiki is built from. Nothing else needs to be installed or scheduled.

## Setup

Put this folder where Hermes loads plugins, or point Hermes at it. In `config.yaml`:

```yaml
plugins:
  enabled:
    - nyx-wiki-recall
  entries:
    nyx-wiki-recall:
      settings:
        wiki_path: /path/to/wiki
```

Set `OPENROUTER_API_KEY` the usual Hermes way. The plugin reads it through Hermes's secret API (`agent.secret_scope.get_secret`).

`questions.txt` sits next to `plugin.yaml`. Edit it to change the questions sent to the decision model, the skip rules, the transcript settings, and every sentence the plugin shows. The plugin reads the file again when it changes. `wiki_read`'s tool description is read when the plugin loads. To keep your edits outside the install folder, set `questions_path` next to `wiki_path`.

`wiki_path` unset, a missing questions file, or a missing key means no glimpses. Transcripts are written in every one of those cases. With no key the plugin logs one warning when it loads, saying lookups are off; it then adds nothing to the prompt and writes no `Wiki pages:` line for any message until the key is set.

Requires Hermes 0.22.5 or newer.

## Start your wiki

The plugin ships an empty wiki to start from in `templates/starter-wiki/`:

```
SCHEMA.md      house rules: page layout, glimpses, citations
index.md       a list of every page
log.md         one entry per change
people/  projects/  topics/  events/    four top folders, each with an _about.md glimpse
_drafts/       pages waiting for approval, hidden from recall
_sources/      unchanged copies of transcripts, hidden from recall
```

Copy it somewhere outside the plugin folder (an update replaces the plugin folder), then point
`wiki_path` at the copy:

```bash
cp -r templates/starter-wiki ~/my-wiki
hermes config set plugins.entries.nyx-wiki-recall.settings.wiki_path ~/my-wiki
```

or in `config.yaml`, under `plugins.entries.nyx-wiki-recall.settings`, set `wiki_path: ~/my-wiki`.
The next message uses it.

Rename or replace the four top folders to fit your life; keep them few, and give each an
`_about.md` with a short glimpse, because a folder without one is never offered. The starter
wiki has no pages yet, so expect `none` under messages in the transcript at first. Pages come
from the transcripts: by hand, or with the archivist skill below.

## The wiki archivist skill

`skills/wiki-archivist/SKILL.md` is a nightly habit for your agent: read one finished day's
transcript, fix the wiki with cited edits, log the run, and report. It was originally written by
Clotho, an archivist agent. While the plugin is enabled, Hermes lists it as
`nyx-wiki-recall:wiki-archivist`. To run it every night after the day is final:

```bash
hermes cron create "30 2 * * *" "Run the wiki archivist for the oldest finished day that log.md does not mark archived." --skill nyx-wiki-recall:wiki-archivist --name "Wiki archivist"
```

The skill explains the `Wiki pages:` tags, the starter layout, the lock and restore point each
run makes, a day-one guide for an empty wiki, and how to keep costs down.

## Hook or memory provider

`provider = no` in `questions.txt` (the shipped value) runs NyxWikiRecall as a hook beside whatever memory provider Hermes already uses.

`provider = yes` lets NyxWikiRecall serve as Hermes's memory provider instead. The plugin never changes Hermes's settings, so you also choose it in Hermes the normal way and restart Hermes:

```bash
hermes memory setup                           # pick nyx-wiki-recall
# or
hermes config set memory.provider nyx-wiki-recall
```

Both have to agree. If they don't, the plugin logs a warning that says which one to change, and stays on the hook:

- `provider = yes` but Hermes's `memory.provider` is something else: glimpses come from the hook.
- Hermes's `memory.provider` is `nyx-wiki-recall` but `provider = no`: the provider reports itself unavailable (with the reason) and glimpses come from the hook.

In both positions the hook stays loaded (`kind: standalone` in `plugin.yaml`). With `provider = yes` the hook does the lookup and hands it to the provider, so each turn is looked up once and the same messages are eligible.

## Transcripts

Every turn is recorded from the hooks Hermes already calls: `pre_llm_call` gives the message (with `session_id`, `turn_id`, `platform`, `sender_id`), and `post_llm_call` gives the reply and the turn's messages, so tool calls can be listed. This works on every platform Hermes serves (Discord, Telegram, Slack, the CLI, and the rest). Turns from `cron` and `subagent` are left out by default (`transcript.skip.platforms`).

Scheduled (cron) jobs also get no wiki lookup by default: a lookup costs a decision-model call and adds glimpses to the job's prompt, which an archivist job reading the wiki directly does not need. `lookups.on_cron = yes` turns lookups back on for cron turns. A turn counts as cron when Hermes runs it from its scheduler (the hook's `platform` is `cron`, or Hermes bound `HERMES_CRON_SESSION` for the job). This setting does not change what is recorded; `transcript.skip.platforms` does that.

After each reply, today's file is rebuilt off the reply path. It is one file per local day, named like `Mar-04-2026-transcript.md`, in `transcripts/` inside the plugin's data folder (`<Hermes home>/plugin-data/nyx-wiki-recall/`, the folder Hermes documents for plugin data). `transcript.folder` moves it. `transcriptIndex.md` in the same folder lists the days; a summary someone writes into its third column is kept.

Each file starts with a header:

```
# March 4, 2026

**Status:** open
**Messages:** 12
**Sessions:** 2
**Exported:** 2026-03-04 18:20:11
**Exporter: nyx-wiki-recall**
**Body-SHA256:** ...
**Source:** Hermes turns recorded by nyx-wiki-recall (discord, telegram)
```

then one `## Platform / chat name` section (or `## Platform (session id)` when Hermes names no chat) per stretch of conversation, and one `### Speaker (YYYY-MM-DD HH:MM:SS)` block per message. In a shared chat (Hermes's chat type `group`, `channel` or `thread`) Hermes puts the sender's display name in front of the message, like `[Robin]`; that name is the speaker. In a direct message or the CLI Hermes adds no such tag, so a leading `[draft]` or `[1]` there is part of the message and stays in its text. There the speaker is the display name the gateway bound for the turn (`HERMES_SESSION_USER_NAME`), and with none, such as the CLI, `transcript.user.name` (default `User`). The chat type and names are read with `gateway.session_context.get_session_env`, the way Hermes's plugin guide documents; when the chat type is unknown the message is treated as a direct message. Replies are from `transcript.assistant.name` (default `Assistant`). `transcript.alias = display name -> name` relabels a display name. Hermes's own heartbeat turns are labelled `Heartbeat`, even when Hermes put the last sender's name in front.

Under each message the plugin scored, a `Wiki pages:` block says what happened:

- numbered `path (score)` lines: the pages shown;
- `none`: scored, nothing reached `threshold`;
- `ineligible`: a skip rule matched;
- `not scored`: the decision model failed or timed out.

A message with no block was not looked at (for example, no wiki was set, no key, or a cron turn). The block comes from the suggestions folder (`suggestions.folder`, default `suggestions/` in the data folder; `off` writes none), one file per day named `YYYY-MM-DD.jsonl`. Each lookup appends one line to that day's file: hashes of the message (not the message), whether it was scored, the pages with scores, and `error` when the decision model failed. A day's transcript reads only that day's file and the two next to it. An older single `wiki_suggestions.jsonl` is no longer read or written; the plugin notes it once in the log and leaves it for you to delete.

Common API-key and webhook-URL shapes are replaced with `[redacted]` in the journal and the transcript.

`post_llm_call` fires only for turns that finish; an interrupted turn keeps its message and has no reply.

`transcript = no` turns transcripts off. The built-in writer is the only one: there is no setting to hand transcripts to another script. (An older `transcript.script` line is ignored, with one warning in the log.)

## Finishing each day

With `finalize = yes` (the default), a background thread writes the day that just ended one last time at `finalize.after` (default 02:00), with `**Status:** final`, and notes it in `finalize.state` (default `finalized.json` in the data folder). A final day is never written again, and a file the plugin did not write is never replaced. Days missed while Hermes was off are written at the next check, oldest first, at most `finalize.catchup.days` (default 7). `finalize.from` sets the earliest day the plugin may finish. The first run only claims days that finish after it starts. The thread runs in both positions of `provider` and does not wait for a turn.

### Old working files

The journal (`journal/YYYY-MM-DD.jsonl`) and the suggestions (`suggestions/YYYY-MM-DD.jsonl`) are one file per day, so they never grow into one big file, but the folders keep a file for every day. `keep.days` in `questions.txt` removes old ones:

- `keep.days = 0` (the shipped value) keeps everything. The plugin deletes nothing.
- `keep.days = 30` deletes the journal and suggestions files of finished days more than 30 days old, right after the nightly check.

Only files named `YYYY-MM-DD.jsonl` in those two folders are deleted, and only for a day that `finalize.state` records as finished (final or empty) and whose transcript file still says `Status: final`. A day that is not finished, a day whose transcript someone else wrote, and the newest finished day (the next day still reads it) are kept. The daily transcripts, `transcriptIndex.md`, `finalize.state` and the wiki are never deleted. Each removal is logged by name. Two Hermes processes on one home take turns through a lock file, so they never clean at once. It needs `finalize = yes`.

## What the reader sees

Each glimpse is the `[nyx.glimpse]` section of `questions.txt`. The shipped section is:

```
### {title}

{glimpse}

Match confidence: {score}
File: {path}
Short name: {slug}
```

`{score}` is the decision model's score with two decimals. A line with `{score}` is left out when a page has no score. Inside a section, a line that starts with `#` and a space is a note for the editor and is not sent; a line that starts with `##` or `###` is a heading and is sent.

`max.pages` caps the pages per message, highest score first. The shipped value is 8. `0` or blank means no cap.

## Wiki shape

- A folder is a category. Its glimpse is the text between the glimpse markers in `_about.md`.
- Any other `.md` file in the folder is an article. Its glimpse is the text between the same markers. A page with no markers uses its first paragraph.
- The first request asks about top-level folders.
- The second request asks about whatever sits one level down: folders, articles, or both.
- The third request asks only about articles directly inside a folder the second request opened.
- Folder names that start with `.` or `_` are unread when `skip.hidden = yes`. Add `skip.dir` lines for other folder names.

`examples/wiki` is a small wiki for the tests. It is not a real notebook. Start a real wiki from `templates/starter-wiki` (see Start your wiki).

## What gets skipped

`questions.txt` holds the rules. The shipped file skips an empty message, a slash command, a one-line reply such as thanks or hi, and Hermes's own heartbeat and background-process turns. Add a `skip.line` for a category of message whose first line should get no lookup. Nothing in the Python names a particular wiki or a particular category of message.

`context.turns` sets how many exchanges the decision model sees. The shipped file uses 2: the previous message, the replies that followed it, and the new message. `skip.turn` lists messages left out of that conversation, such as a context-compaction handoff. The plugin logs each score, the skip reason, and the pages it attached. The log names the page and the score, and leaves out the message text and the glimpse text.

## What this plugin does outside its own folder

- **Network.** For each message that passes the skip rules, it sends HTTPS requests to OpenRouter's Decisions API (`decisions.url`, default `https://openrouter.ai/api/alpha/decisions`) with your `OPENROUTER_API_KEY`. Each request carries the recent conversation, trimmed to `message.chars` (default 20,000 characters, newest kept), and the titles, paths and glimpses of the wiki folders and pages being asked about. A message can take up to three requests (top folders, one level down, articles), all within `budget.seconds`. Nothing else is sent anywhere. No telemetry.
- **Files written**, all in `<Hermes home>/plugin-data/nyx-wiki-recall/` unless you set another path:
  - `journal/YYYY-MM-DD.jsonl`: the text of each recorded message and reply, with the session id, turn id, platform, sender id and chat type. This is what the transcripts are built from.
  - `transcripts/*-transcript.md` and `transcripts/transcriptIndex.md`.
  - `suggestions/YYYY-MM-DD.jsonl`: message hashes, page paths and scores.
  - `finalized.json`: the days already finished.
  - `.nyx-wiki-recall-cleanup.lock`: held only while `keep.days` removes old files.
  - `transcripts/.nyx-wiki-recall.lock`: held while a file is written, so two Hermes processes on one home do not write at once.
- **Files deleted.** None by default. With `keep.days` above 0, the plugin's own old `journal/` and `suggestions/` day files, as described under Old working files.
- **Files read** outside the plugin folder: the wiki folder (`wiki_path`); Hermes's config, read-only (this plugin's `settings` entry and `memory.provider`); the session values the gateway binds for the turn (`HERMES_SESSION_PLATFORM`, `HERMES_SESSION_USER_NAME`, `HERMES_SESSION_CHAT_NAME`, `HERMES_SESSION_CHAT_TYPE`, and `HERMES_CRON_SESSION`); and, only in memory-provider mode when the hook left no lookup for the turn, the last 40 messages of the current session from Hermes's `state.db`, opened read-only.
- **Background threads.** One day-finishing thread per Hermes process, which wakes every `finalize.check.minutes` (default 10). One short-lived thread that rewrites today's transcript after a reply and exits when it is done.
- **Hermes settings.** The plugin does not write Hermes's config or any other Hermes file. It runs no shell commands.

## Known limitations

- A reply written after midnight lands at the top of the next day's transcript.
- On Windows, two Hermes processes writing very long messages at the same moment can mix their lines in the journal. A damaged line is dropped.
- Missed days are finished only within `finalize.catchup.days` (default 7). A day further back is never written as final.
- In memory-provider mode, two chats at once may occasionally run an extra decision-model lookup. The output is never wrong.
- The decision-model timeout applies to each network step, so a stalled DNS lookup can go past `budget.seconds`. A turn can wait up to about 20 seconds by design.
- The wiki folder is rescanned on each message (one timestamp check per file). That is fine for hundreds of pages.
- In the repeated hour when clocks go back, times in a transcript can look out of order, though the real order of the messages is kept.
- Redaction is pattern-based: common key and token shapes are caught, a plain-word password is not. The decision model receives the conversation unredacted.
- A message that copies a transcript's headings looks the same as a real heading in the transcript.
- `questions.txt` inside the plugin folder is replaced by `hermes plugins update`, unless `questions_path` points to a copy elsewhere.
- In a group where Hermes keeps one session per person (`group_sessions_per_user`, Hermes's default), Hermes adds no `[Name]` tag, but the chat type is still a group, so a leading `[...]` that the person typed is read as a name there. The display name from the gateway is used otherwise.
