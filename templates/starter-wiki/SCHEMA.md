# Wiki schema

This file is the house rules for this wiki. Anyone who edits the wiki, person or agent, reads it
first. Change it to suit you. When it disagrees with a skill or a guide, this file wins.

NyxWikiRecall never shows this file to your agent. Markdown files in the wiki's top folder are
guides to the wiki, not pages.

## How the wiki is laid out

```
SCHEMA.md        these rules
index.md         a list of every page, by folder
log.md           one entry per change to the wiki, newest last
people/          the people in your agent's life
projects/        things being made or worked toward
topics/          ideas, preferences, how-tos, and knowledge
events/          things that happened on a date
_drafts/         pages waiting for approval (never shown to the agent)
_sources/        unchanged copies of transcripts and other records (never shown)
```

- A **folder** is a category. Its `_about.md` holds the folder's glimpse.
- Any other `.md` file in a folder is a **page**.
- Keep it shallow: a top folder, at most one level of sub-folders, then pages.
  NyxWikiRecall asks about the top folders, then about what sits one level down, then about the
  pages inside a sub-folder it opened. A page deeper than that is never offered.
- A folder or file whose name starts with `_` or `.` is hidden from recall
  (`skip.hidden = yes` in `questions.txt`). That is what keeps `_drafts/` and `_sources/` private.
  To hide a folder without renaming it, add a `skip.dir = <name>` line to `questions.txt`.
- File names are lowercase with dashes: `people/sam-rivera.md`, `projects/kitchen-garden.md`.
  The file name without `.md` is the page's short name, which the agent passes to `wiki_read`.

## A page

```markdown
---
title: "Sam Rivera"
updated: 2026-03-04
---
<!-- glimpse -->
Sam Rivera is a friend from the climbing gym who is learning Spanish and asks the agent for
weekly practice. Sam prefers short exercises and gentle corrections.
(2026-03-04, src: _sources/transcripts/Mar-04-2026-transcript.md#L42)
<!-- /glimpse -->

# Sam Rivera

The full page goes here: details, history, and anything the glimpse leaves out.

## Related

- [[spanish-practice]]
- [[climbing]]
```

### The glimpse

The text between `<!-- glimpse -->` and `<!-- /glimpse -->` is the only part NyxWikiRecall reads
when it decides whether a page fits a conversation, and the part the agent sees when it does.
The rest of the page is read only when the agent calls `wiki_read`.

- Two to four sentences. Name the person or thing; never rely on "she" or "this".
- Say what tells this page apart from its neighbours.
- End with a citation.
- A page with no markers uses its first paragraph as its glimpse. Markers are clearer.

### Citations

Every fact traces to a record. Write the date of the record, then the file and line:

```
(2026-03-04, src: _sources/transcripts/Mar-04-2026-transcript.md#L42)
```

The path is relative to the wiki's top folder. `#L42` is the line number in that file. Copy
transcripts into `_sources/` unchanged, so the line numbers stay true.

## A folder

Each visible folder has an `_about.md`:

```markdown
---
title: "People"
---
<!-- glimpse -->
One or two sentences that say what belongs in this folder.
<!-- /glimpse -->

# People
```

A folder without a glimpse is never offered, and nothing inside it is either. The top folders
steer everything: a conversation reaches a page only after its top folder's glimpse matched.
Keep the top folders few and their glimpses short and distinct.

## Changes

- Every change gets an entry in `log.md`.
- Add new pages to `index.md` in the same edit.
- Never write a password, API key, or token into the wiki. Write `[redacted]` instead.
