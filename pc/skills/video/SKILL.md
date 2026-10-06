---
name: video
description: Video with ffmpeg — read its parameters, cut a segment, compress, change the format (gif too), crop to vertical 9:16 for Reels/Shorts, pull out the audio, take a preview frame. For editing, preparing clips for social networks and converting.
---

# Skill: working with video (ffmpeg)

The `video` tool does the common editing operations through ffmpeg. It needs ffmpeg installed
(ffprobe comes with it); without it, the tool says plainly what to install.

## Order

1. **`info` first.** `video operation=info path=...` — the length, resolution, codecs, size. They
   tell what to do and how (for example, whether to compress, which aspect ratio).
2. Then the operation you need. The result is saved to the working folder next to the source (the
   name is chosen for you, or set `output`); the source is not changed.

## Operations

* `trim start=00:00:05 end=00:00:20` — cut a segment (fast, no re-encoding).
* `compress` — make the file smaller (CRF 28, x264). Heavy clips shrink many times over; short
  synthetic ones may barely change.
* `convert format=webm|mp4|mkv|gif` — change the format. `gif` makes a light animation (12 fps,
  480 wide).
* `vertical` — crop around the centre to 9:16 (1080×1920) for Reels/TikTok/Shorts.
* `extract_audio` — pull the sound out to mp3 (for example, to transcribe it).
* `frame time=00:00:03` — take a preview frame (a cover).

## Details

* Time is `HH:MM:SS` (`00:05` or plain seconds work too).
* Heavy operations (compressing or converting a long video) take a while — that is normal; do not
  repeat the call, wait for the result.
* To transcribe speech from a video — `extract_audio` first, then work with the audio file.
