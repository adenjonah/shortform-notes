---
name: reel
description: Import an Instagram Reel, TikTok or YouTube Short into a Markdown note via reelnotes. Use when the user pastes such a link or invokes /reel <url>.
---

# /reel <url>

Run the reelnotes pipeline on the link and report back.

1. Run: `reelnotes "<url>" --json` (add `-o <dir>` if the user named a folder).
2. Parse the JSON. On `error`, tell the user what failed (Instagram private/deleted, yt-dlp blocked, etc.) and stop.
3. Reply with the note path, the title, the summary, and the takeaways as bullets. Mention any `warnings`.
4. If the user asks follow-up questions about the video, read the note file — it contains the verbatim caption and transcript.

Never paste the raw transcript unless asked; the note has it.
