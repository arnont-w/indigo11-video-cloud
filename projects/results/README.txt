Finished files go here, sorted by job type, then by project (one folder per project):
  results/<type>/<YYMMDD>-<project>/

Types:
  video-creation        a whole video made from a topic (video + caption.txt)
  editing               cleaned / cut / captioned footage (mp4, SRT)
  reverse-engineering   a reference video studied or copied (SPEC.md, style pack)
  transcription         captions and transcripts (srt, json)

Not here: music, sound effects, images, icons, videos, fonts and templates you keep to reuse. They go into the matching folder of assets/
(Claude saves them with _system/engine/save_asset.py), so the search finds them first next time.

Never put files loose in this folder. Claude saves them with _system/engine/save_result.py.
