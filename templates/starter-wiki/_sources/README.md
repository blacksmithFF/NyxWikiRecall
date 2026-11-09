# Sources

Unchanged copies of the records the wiki is built from: daily transcripts in `transcripts/`,
and anything else you cite. The folder name starts with `_`, so NyxWikiRecall never shows these
files to the agent.

- Copy a file here as it is. Do not edit it, so line numbers in citations stay true.
- Copy only finished transcripts: ones whose header says `**Status:** final`. A day becomes
  final after `finalize.after` (default 02:00) the next morning.
- Cite a line like this: `(2026-03-04, src: _sources/transcripts/Mar-04-2026-transcript.md#L42)`.

NyxWikiRecall writes the transcripts by itself, by default to the `transcripts/` folder in
`<Hermes home>/plugin-data/nyx-wiki-recall/` (usually
`~/.hermes/plugin-data/nyx-wiki-recall/transcripts/`). `transcript.folder` in `questions.txt`
moves it.
