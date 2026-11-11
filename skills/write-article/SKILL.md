---
name: write-article
description: >-
  Writes a web article from a session transcript that sums up the key points
  and takeaways, and saves one or two stills from the video next to it. Use
  when the user asks for an article, a blog post, a write-up, or a summary of
  the session for the web.
---

# Write an article

Run from the repository root. This step reads the transcript and writes an article. It does not transcribe, brief, or cut. If the transcript is missing, run `transcribe-session` first.

1. Run `python -m shortgen path "<video>"`. Read the file at `transcript_md`. On a long file, read it in parts.
2. Pick the key points and the takeaways. Note the timecode of the one or two moments you want as images.
3. Save the images:

```bash
python -m shortgen frame "<video>" --at 00:02:31.000 --name hero
python -m shortgen frame "<video>" --at 00:06:10.500 --name second
```

Each command prints the path, `generated/<slug>/article/<name>.jpg`. Use one image, or two. Not more.

4. Write the article to the `article` path from step 1, `generated/<slug>/article/article.md`. Reference the images by file name, for example `![alt](hero.jpg)`.

## What to write

- Plain and dry. Short sentences. No hype, no exclamation marks, no "in today's fast-moving world".
- Open with the main point in the first two sentences. Do not open with the event, the date, or thanks.
- 400–700 words, unless the user gave a length.
- Structure: title, a one-paragraph summary, 3–5 key points under short headings, then a "Takeaways" list of 3–5 bullets.
- Use only what was said. Do not add facts, figures, or names that are not in the transcript. If a speaker is `speaker_0`, say "the host" or "a speaker". Use a name only if `speakers.json` maps it.
- A direct quote must be copied word for word from the transcript. Leave out filler and false starts, and do not change the meaning.
- Transcript errors (names, product terms) may be wrong. When unsure, say it plainly or leave it out.

## Pick the images

- Choose a moment where a speaker or slide is clearly in frame, not a transition, a blink, or a black frame.
- Take the first from near the main point. Take the second from a later key point, or skip it.
- Look at each image after saving. If it is poor, pick a time a second or two away and run `frame` again with the same `--name`.

## Shape

```markdown
# <Title: the claim, not the event>

![<what the picture shows>](hero.jpg)

<Summary paragraph.>

## <Key point>

<Two to four sentences.>

## Takeaways

- <One line each.>
```
