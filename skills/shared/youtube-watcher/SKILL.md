---
name: youtube-watcher
description: Fetch and read transcripts from YouTube videos. Use when you need to summarize a video, answer questions about its content, or extract information from it.
author: michael gathara
version: 1.0.0
triggers:
  - "watch youtube"
  - "summarize video"
  - "video transcript"
  - "youtube summary"
  - "analyze video"
metadata: {"clawdbot":{"emoji":"📺","requires":{"bins":["yt-dlp"]},"install":[{"id":"brew","kind":"brew","formula":"yt-dlp","bins":["yt-dlp"],"label":"Install yt-dlp (brew)"},{"id":"pip","kind":"pip","package":"yt-dlp","bins":["yt-dlp"],"label":"Install yt-dlp (pip)"}]}}
---

# YouTube Watcher

Fetch transcripts from YouTube videos to enable summarization, QA, and content extraction.

## How To Use This Skill

**CRITICAL:** Read this section carefully before running any commands.

1. The script is located at `scripts/get_transcript.py` inside this skill directory
2. There is NO file named `youtube_watcher.py` — do not attempt to run it
3. You MUST pass a YouTube URL as the first argument to the script
4. You MUST use a timeout (300 seconds recommended) because transcript fetching is slow

**Correct command format:**
```bash
shell(command="python3 {baseDir}/scripts/get_transcript.py 'https://www.youtube.com/watch?v=VIDEO_ID'", timeout_seconds=300)
```

**Wrong — do NOT use:**
```bash
uv run youtube_watcher.py          # This file does not exist
python youtube_watcher.py          # This file does not exist
get_transcript.py                  # Missing path and URL
```

## Usage

### Get Transcript

Retrieve the text transcript of a video.

```bash
python3 {baseDir}/scripts/get_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

**Important:** For long videos, this command may take 2-5 minutes to complete. Always pass `timeout_seconds=300` (or higher) to avoid timeouts:

```bash
timeout_seconds=300 python3 {baseDir}/scripts/get_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Examples

**Summarize a video:**

1. Get the transcript (always use timeout_seconds=300 or higher):
   ```bash
   timeout_seconds=300 python3 {baseDir}/scripts/get_transcript.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
   ```
2. Read the output and summarize it for the user.

**Find specific information:**

1. Get the transcript (use timeout_seconds=300 or higher):
   ```bash
   timeout_seconds=300 python3 {baseDir}/scripts/get_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"
   ```
2. Search the text for keywords or answer the user's question based on the content.

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `youtube_watcher.py: No such file or directory` | You're using the wrong filename | Use `scripts/get_transcript.py` instead |
| `the following arguments are required: url` | You forgot to pass the YouTube URL | Add the URL as the first argument |
| `command timed out` | Video is long and default timeout too short | Use `timeout_seconds=300` or higher |
| `yt-dlp: command not found` | `yt-dlp` is not installed | Install via `brew install yt-dlp` or `pip install yt-dlp` |
| `This video does not have subtitles` | Video has no captions/transcript | Inform the user the video cannot be transcribed |

## Notes

- Requires `yt-dlp` to be installed and available in the PATH.
- Works with videos that have closed captions (CC) or auto-generated subtitles.
- If a video has no subtitles, the script will fail with an error message.
- **Timeout:** YouTube transcript fetching can be slow. Always use `timeout_seconds=300` (5 minutes) or higher to avoid command timeouts, especially for long videos.
