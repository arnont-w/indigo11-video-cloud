# CapCut Desktop project JSON (reverse-engineered from v8.5)

How CapCut projects are laid out, for patching without an official API. The schema differs between major versions: inspect a fresh project made in CapCut's own UI before assuming. Verified on macOS 8.5 (May 2026) and Windows 8.7; older (6.x) and newer (9.x+) are untested.

**Windows differences:** drafts folder `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\`; the timeline file is `draft_content.json`, not `draft_info.json` (read every `draft_info.json` below as `draft_content.json`). macOS: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/`. The scripts resolve this (override with `CAPCUT_DRAFTS_DIR`).

## Folder layout

```
com.lveditor.draft/
├── root_meta_info.json            parent catalogue
└── <project>/
    ├── draft_info.json (+ .bak)   root copy (mirror target)
    ├── draft_meta_info.json       project metadata + Media panel
    ├── template-2.tmp             rollback
    └── Timelines/
        ├── project.json (+ .bak)  timeline catalogue
        └── <UUID>/                the real timeline
            ├── draft_info.json (+ .bak)   READ ON OPEN: patch this
            └── template.tmp, template-2.tmp
```

## The 9 sync points

Mirror every timeline patch to all nine, or CapCut self-heals to the old state within about 30 s:
1. `<project>/draft_info.json`  2. `.../draft_info.json.bak`  3. `<project>/template-2.tmp`
4. `<project>/draft_meta_info.json` (patch `tm_duration`)
5. `<project>/Timelines/<UUID>/draft_info.json` (canonical, what Open reads)
6. its `.bak`  7. `Timelines/<UUID>/template-2.tmp`  8. `Timelines/<UUID>/template.tmp`
9. `../root_meta_info.json` (match by `draft_fold_path`, update `tm_duration`)

CapCut also auto-saves over the file every ~30 s while running, so it must be quit before any write. The scripts mirror and back up for you.

## `draft_meta_info.json`: Media panel

Key fields: `draft_fold_path`, `draft_id`, `draft_name`, `tm_duration` (total timeline duration in **microseconds**), `tm_draft_create` / `tm_draft_modified` (epoch microseconds), and `draft_materials[]`. Each group has `type` (0 = video; others audio, image, text) and `value[]` rows: `id`, `file_Path`, `extra_info` (file name), `metetype`, `width`, `height`, `duration` (us), `create_time`, `import_time`, `import_time_ms`, plus empty bookkeeping fields. **Ghost entries**: a cancelled drag leaves rows with empty `file_Path` and `duration: 33000`; always filter them out.

## `Timelines/<UUID>/draft_info.json`: the timeline

Top-level keys (v8.5): `id`, `version` (360000), `new_version` ("167.0.0"), `name`, `duration` (us), `create_time`, `update_time`, `fps`, `color_space`, `draft_type` ("video"), `config`, `canvas_config` (width, height, ratio), `materials`, `tracks`, `keyframes`, `keyframe_graph_list`, `group_container`, `platform` and `last_modified_platform` (must declare app version 6.0+ or CapCut rejects the file), plus assorted info blocks (`time_marks`, `relationships`, `cover`, `extra_info`, `path`).

**`materials`** is the asset catalogue: `videos[]` (`id`, `path`, `duration`, `width`, `height`, `material_name`), `texts[]`, `audios[]`, `canvases[]`, `material_animations[]`, `placeholder_infos[]`, `speeds[]`, `sound_channel_mappings[]`, `material_colors[]`, `vocal_separations[]`. Every entry has its own UUID `id`, and track segments point to it with `material_id`.

**`tracks`** is a list of `{id, type ("video" | "text" | "audio" ...), attribute, segments[]}`. A segment holds `id`, `material_id`, `extra_material_refs[]`, `source_timerange {start, duration}` (position in the source media, us), `target_timerange {start, duration}` (position on the timeline, us), `clip {scale {x,y}, transform {x,y}, rotation, ...}`, `render_index` and more. The `source` to `target` pair is what lets captions be remapped onto a hand-cut timeline.

**Helpers (`extra_material_refs`).** Each video segment references one helper from each of six categories: canvases, placeholder_infos, speeds, sound_channel_mappings, material_colors, vocal_separations. They are created **per segment**, so cloning a video segment means cloning its six helpers with new UUIDs and updating the refs. A text segment references one helper (a `material_animation`).

**Text content gotcha.** `texts[i].content` is a JSON **string** holding the style, for example `{"styles":[{"fill":{"content":{"solid":{"color":[1,1,1]},"render_type":"solid"}},"range":[0,10],"size":15,"font":{"path":"","id":""}}],"text":"สวัสดี"}`. Build the style object, `json.dumps(..., ensure_ascii=False)` for Thai, and assign the string to `.content`. Multi-colour (karaoke) text needs `useLetterColor: true` in each styled range, or CapCut renders one colour.

## Quirks

- `draft_info.json` can be 0 bytes after a first save of an empty timeline; the Media panel is still committed to `draft_meta_info.json`. The same applies to `Timelines/<UUID>/draft_info.json`. A 0-byte draft means a blank timeline (Flow A), not an error.
- Latin characters inside Thai text stay as UTF-8 in the inner text string.
- Leave the font path empty: CapCut picks a Thai-capable font itself, and the value is internal.
- IDs are lowercase UUIDs. Library resource ids (stickers, effects, filters) change per app version and region, so harvest them from the user's own machine instead of hard-coding them.
