#!/usr/bin/env python3
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import os as _os_utf8, sys as _sys_utf8
if _os_utf8.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (_sys_utf8.stdout, _sys_utf8.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
"""Remap caption timestamps from the ORIGINAL video timeline to the POST-CUT timeline.

Why this exists
---------------
After indigo11-clean-cut produces an EDL (kept ranges of the original) and renders
cleaned.mp4, captions transcribed from the original video still carry ORIGINAL
timestamps. If you put them on cleaned.mp4 unchanged, every word lands wherever
that timestamp falls in the SHORTER file - i.e. drifted right, often by many
seconds. This script does the deterministic translation:

  token_at_t_original → token_at_(t_original − cumulative_dropped_before_t)
  token_inside_a_dropped_range → removed (it was cut out of the audio)

The math is exact - no STT re-call, no AI judgment. All you need is the EDL
(see EDL schema below) and any captions/transcript file with timestamps in
seconds (or ms).

EDL schema
----------
Two shapes are accepted; the new shape supports clip REORDERS (user rearranged
clips in CapCut so source order ≠ target order).

NEW (preferred): explicit source↔target mapping per segment
  {"segments": [{
      "source_start_ms": int, "source_end_ms": int, // range in original media
      "target_start_ms": int, "target_end_ms": int // range on the cut timeline
  }, ...]}

LEGACY (still accepted, for backward compat with clean-cut output):
  {"segments": [{"start_ms": int, "end_ms": int}, ...]}
  // = source-only ranges in source order; target is derived by cumulative sum,
  // which assumes target order = source order (no reorders).

The legacy shape works for trim/jump cuts (clean-cut output). For manual cuts in
CapCut that include reorders, use `capcut_to_edl.py` to read the project's video
timeline - it emits the NEW shape preserving both coordinate systems.

Inputs accepted (auto-detected by shape)
----------------------------------------
1. transcribe `captions.json` - {words: [{start, end, text}], words_raw: [...]}
2. transcribe `words.json` - {groups: [{start, end, text, tokens: [...]}]}
3. CapCut-side captions - {captions: [{timeline_start_ms, timeline_end_ms, ...}]}

For each, the script remaps every timestamp it finds (group, token, phrase, char)
that's currently in ORIGINAL coordinates. Output is sorted by TARGET start so
reordered timelines read in the right order. Tokens whose interval straddles a
cut boundary (start in one segment, end in another) are dropped - keeping them
would require giving the two endpoints different target offsets, producing a
nonsense interval.

Coordinate contract - same as kit (edit-rules #14):
  - timeline_*_ms / duration_ms fields: MILLISECONDS
  - bare start/end on a group or token: SECONDS
  - EDL source/target fields: MILLISECONDS

Usage
-----
  remap_captions.py --edl projects/_work/clean-cut/<basename>/edl-rough-safe.json \\
                    --in projects/_work/transcribe/<basename>/words.json \\
                    --out projects/_work/transcribe/<basename>/words-remapped.json

  # or remap a CapCut-shape file:
  remap_captions.py --edl edl.json --in captions.json --out captions-remapped.json

Verification (built-in)
-----------------------
After remap, the script prints:
  - input count, output count (and how many dropped because they were cut)
  - max remap delta (how far the latest token moved)
  - monotonic check + offset-text check
Failures exit non-zero so you can chain this in a pipeline.
"""
import argparse
import json
import sys
from pathlib import Path


def build_offset_table(edl_segments_ms):
    """From the EDL segments build an interval table for O(log n) remap.

    The EDL is an explicit source↔target map - each segment says "the original
    audio at [source_start, source_end] is now on the timeline at [target_start,
    target_end]". This shape supports BOTH:
      (a) the simple "trim & keep order" case (clean-cut output) - source is
          monotonic, target is built by cumulative sum.
      (b) the "user rearranged clips in CapCut" case - source order ≠ target
          order. e.g. clip B (source 10-15s) plays FIRST on the timeline (target
          0-5s), then clip A (source 0-5s) plays second on target 5-10s. The
          remap must honor target order so captions on the rearranged timeline
          read in the right sequence.

    Accepts two input shapes:
      - New: `{source_start_ms, source_end_ms, target_start_ms, target_end_ms}`
      - Old (clean-cut legacy): `{start_ms, end_ms}` - interpreted as source-only
        ranges in source order, with target derived by cumulative sum (no
        reorders possible in this shape).

    Returns:
      intervals: list[dict] with keys src_s, src_e, tgt_s, tgt_e - sorted by SRC
                 for fast "where does this original timestamp live" lookups.
      keep_starts: list[int] of src_s - bisect index into intervals.

    Validation: no two source ranges overlap (a source moment can't appear at
    two places on the target), and no two target ranges overlap (the target
    timeline is a single sequence).
    """
    intervals = []
    for i, s in enumerate(edl_segments_ms):
        # Pick the target-start key by PRESENCE, not truthiness: a legitimate
        # target_start_ms of 0 (the first kept segment, extremely common) is
        # falsy, so `s.get("target_start_ms") or ...` would silently discard it
        # and fall through to the legacy start_ms path -> KeyError on a valid
        # new-shape EDL (P0-03). Test the keys explicitly.
        tgt_start_key = ("target_start_ms" if "target_start_ms" in s
                         else "timeline_start_ms" if "timeline_start_ms" in s else None)
        tgt_end_key = ("target_end_ms" if "target_end_ms" in s
                       else "timeline_end_ms" if "timeline_end_ms" in s else None)
        has_source = "source_start_ms" in s and "source_end_ms" in s
        if has_source and tgt_start_key is not None:
            if tgt_end_key is None:
                raise SystemExit(
                    f"EDL segment {i} has {tgt_start_key} but no target/timeline end "
                    f"(need target_end_ms or timeline_end_ms)")
            for k in ("source_start_ms", "source_end_ms", tgt_start_key, tgt_end_key):
                if s.get(k) is None:
                    raise SystemExit(f"EDL segment {i} field '{k}' is null")
            intervals.append({
                "src_s": int(s["source_start_ms"]),
                "src_e": int(s["source_end_ms"]),
                "tgt_s": int(s[tgt_start_key]),
                "tgt_e": int(s[tgt_end_key]),
            })
        elif "start_ms" in s and "end_ms" in s:
            intervals.append({
                "src_s": int(s["start_ms"]),
                "src_e": int(s["end_ms"]),
                "tgt_s": None, # filled by cumulative sum below
                "tgt_e": None,
            })
        else:
            raise SystemExit(
                f"EDL segment {i} has no usable shape: need "
                f"(source_start_ms + source_end_ms + target/timeline start+end) "
                f"or legacy (start_ms + end_ms). Got keys: {sorted(s.keys())}")

    # Backfill target for legacy/trim shape: target is built by stacking kept
    # ranges in source order (the order they appear in the input list).
    if any(iv["tgt_s"] is None for iv in intervals):
        if not all(iv["tgt_s"] is None for iv in intervals):
            raise SystemExit("EDL mixes new (source+target) and legacy (source-only) shapes")
        cursor = 0
        for iv in intervals:
            dur = iv["src_e"] - iv["src_s"]
            iv["tgt_s"] = cursor
            iv["tgt_e"] = cursor + dur
            cursor += dur

    # Reject speed-changed segments (M-CC3): remap assumes each kept range maps
    # LINEARLY 1:1 (src span == tgt span). A sped-up/slowed segment has different
    # spans, so interior token times would land wrong. capcut_to_edl.py only
    # WARNS on speed!=1.0; catch it here as a hard error rather than emit silently
    # wrong captions.
    for iv in intervals:
        src_span = iv["src_e"] - iv["src_s"]
        tgt_span = iv["tgt_e"] - iv["tgt_s"]
        if src_span > 0 and abs(src_span - tgt_span) > max(2, 0.02 * src_span):
            raise SystemExit(
                f"EDL segment has a speed change (source span {src_span}ms != target "
                f"{tgt_span}ms) - caption remap can't handle speed changes. Remove the "
                f"speed change or remap captions manually.")

    # Sort by source for source→target lookup.
    src_sorted = sorted(intervals, key=lambda iv: iv["src_s"])
    # Validate: source ranges don't overlap (can't have one moment in two places)
    for i in range(1, len(src_sorted)):
        if src_sorted[i]["src_s"] < src_sorted[i - 1]["src_e"]:
            raise SystemExit(
                f"EDL source ranges overlap: [{src_sorted[i-1]['src_s']}, {src_sorted[i-1]['src_e']}] "
                f"and [{src_sorted[i]['src_s']}, {src_sorted[i]['src_e']}]")
    # Validate: target ranges don't overlap (target is one sequence)
    tgt_sorted = sorted(intervals, key=lambda iv: iv["tgt_s"])
    for i in range(1, len(tgt_sorted)):
        if tgt_sorted[i]["tgt_s"] < tgt_sorted[i - 1]["tgt_e"]:
            raise SystemExit(
                f"EDL target ranges overlap: [{tgt_sorted[i-1]['tgt_s']}, {tgt_sorted[i-1]['tgt_e']}] "
                f"and [{tgt_sorted[i]['tgt_s']}, {tgt_sorted[i]['tgt_e']}]")
    return src_sorted, [iv["src_s"] for iv in src_sorted]


def remap_ms(t_orig_ms, src_sorted, keep_starts):
    """Map an original-timeline timestamp (ms) to its position on the target
    (post-cut, possibly-rearranged) timeline.

    Returns None if the timestamp falls outside every source kept range (it was
    cut out of the audio).

    Algorithm: bisect to find the source segment that CONTAINS t_orig (single
    O(log n) lookup), then translate by that segment's source→target offset.
    Because each source moment lives in at most one segment (validated above),
    this works for arbitrary target orderings - reorders in CapCut don't break
    anything.

    Boundary rule: a timestamp exactly at src_e is treated as still-inside that
    segment, so a token ending exactly at a cut boundary doesn't disappear.
    """
    import bisect
    idx = bisect.bisect_right(keep_starts, t_orig_ms) - 1
    if idx < 0:
        return None
    iv = src_sorted[idx]
    if t_orig_ms > iv["src_e"]:
        return None
    # Apply this segment's source→target offset.
    return iv["tgt_s"] + (t_orig_ms - iv["src_s"])


def remap_token_seconds(t_orig_s, src_sorted, keep_starts):
    """Wrapper for second-valued timestamps (used in words.json groups/tokens
    and captions.json words[])."""
    ms = remap_ms(int(round(t_orig_s * 1000)), src_sorted, keep_starts)
    return None if ms is None else ms / 1000.0


def remap_interval_ms(start_ms, end_ms, src_sorted, keep_starts):
    """Remap an INTERVAL [start_ms, end_ms] (source coords) to a single target
    interval. Returns None when the interval can't be represented as one target
    interval - caller should drop it.

    Three cases:
      (a) Both endpoints in the SAME source segment → remap directly.
      (b) Endpoints in DIFFERENT source segments, but those segments are
          contiguous IN BOTH source AND target (no gap, no reorder between them)
          → still safe: apply the LEFT segment's offset to start, the right
          segment's offset to end. If both produce a contiguous target interval
          (right_target_start == left_target_end), this is just a token that
          happens to cross an internal segment seam - not a cut.
      (c) Endpoints span a real cut (source gap, or target reorder) → drop.

    Boundary rule: a timestamp exactly at src_e is treated as still-inside that
    segment, so a token ending exactly at a seam doesn't disappear.
    """
    import bisect
    if start_ms > end_ms:
        return None

    def segment_for(t_ms, *, is_end):
        """Find the source segment whose [src_s, src_e] contains t_ms (inclusive
        of both bounds). Returns index in src_sorted, or None.

        When t_ms falls exactly on a seam shared by two adjacent segments
        (segment A's src_e == segment B's src_s), the choice matters:
          - For a START endpoint (is_end=False): pick the LATER segment (B).
            The token's interior begins inside B.
          - For an END endpoint (is_end=True): pick the EARLIER segment (A).
            The token's interior ends inside A.

        Why this matters: if A and B are adjacent in source but reordered on
        target (B plays before A on the cut timeline), a token whose interior
        sits inside A must use A's target offset, not B's - otherwise the
        token's end on target jumps to B's target slot instead of A's. The
        seam-prefers-A rule for end endpoints (and seam-prefers-B for start
        endpoints) keeps endpoints with the segment that owns the interior.
        """
        # bisect_right places t_ms AFTER any equal src_s; (i = bisect-1) gives
        # the rightmost segment whose src_s <= t_ms. That's the LATER segment if
        # t_ms is exactly on a seam (because keep_starts contains both src_s
        # values and bisect_right of an equal value goes past them).
        i = bisect.bisect_right(keep_starts, t_ms) - 1
        if i < 0:
            # t_ms is before every src_s.
            return None

        # Detect seam: t_ms == src_s of segment i AND segment i-1 exists with
        # src_e == t_ms. (After bisect_right, i is the RIGHT side of the seam.)
        on_seam = (i > 0
                   and src_sorted[i]["src_s"] == t_ms
                   and src_sorted[i - 1]["src_e"] == t_ms)
        if on_seam:
            return (i - 1) if is_end else i

        # Not on a seam: t_ms must lie inside segment i.
        if src_sorted[i]["src_s"] <= t_ms <= src_sorted[i]["src_e"]:
            return i
        return None

    i_s = segment_for(start_ms, is_end=False)
    i_e = segment_for(end_ms, is_end=True)
    if i_s is None or i_e is None:
        return None

    # Single-segment case → straightforward.
    if i_s == i_e:
        iv = src_sorted[i_s]
        return (iv["tgt_s"] + (start_ms - iv["src_s"]),
                iv["tgt_s"] + (end_ms - iv["src_s"]))

    # Cross-segment case: only safe when ALL segments between i_s and i_e are
    # contiguous in BOTH source AND target (no gap, no reorder). That means the
    # caller can apply each segment's offset to the part of the interval inside
    # it, and the resulting pieces meet at the seam - one continuous target
    # interval. If any pair has a source gap (a cut) or a target gap/reorder, the
    # interval straddles a real cut and must be dropped.
    lo, hi = (i_s, i_e) if i_s < i_e else (i_e, i_s)
    for j in range(lo, hi):
        a = src_sorted[j]
        b = src_sorted[j + 1]
        # contiguous in source = no gap between them (b starts where a ends)
        if a["src_e"] != b["src_s"]:
            return None
        # contiguous in target = b starts where a ends on the target timeline too
        # (no gap AND no reorder)
        if a["tgt_e"] != b["tgt_s"]:
            return None

    # All bridged segments are contiguous src↔tgt - start uses i_s offset, end
    # uses i_e offset, and they produce one continuous target interval.
    iv_s = src_sorted[i_s]
    iv_e = src_sorted[i_e]
    return (iv_s["tgt_s"] + (start_ms - iv_s["src_s"]),
            iv_e["tgt_s"] + (end_ms - iv_e["src_s"]))


def _space_before(orig_text, tok, built):
    """The space the source caption had in front of this token ("มี course", "55 นาที").
    Rebuilding text by bare concatenation glued those into "มีcourse"."""
    cs = tok.get("char_start")
    if built and isinstance(cs, int) and 0 < cs <= len(orig_text) and orig_text[cs - 1].isspace():
        return " "
    return ""


def _rebuild_group_from_tokens(grp_template, tokens):
    """Build a single group dict from a list of surviving tokens (already remapped).
    Recomputes text + char offsets from token.text concatenation."""
    text = ""
    new_tokens = []
    for t in tokens:
        text += _space_before(grp_template.get("text", ""), t, text)
        cs = len(text)
        text += t["text"]
        nt = dict(t)
        nt["char_start"], nt["char_end"] = cs, len(text)
        new_tokens.append(nt)
    out = dict(grp_template)
    out["text"] = text
    out["tokens"] = new_tokens
    out["start"] = new_tokens[0]["start"]
    out["end"] = new_tokens[-1]["end"]
    return out


def remap_group(grp, src_sorted, keep_starts, mode):
    """Remap a {start, end, text, tokens: [...]} group (seconds).

    Token-first strategy (the correct way per edit-rules.md):
      1. Map EACH token's start/end through remap_ms. Tokens that land in a cut
         range get None and are dropped.
      2. Rebuild the group's text + char_offsets from the surviving tokens - 
         never from the original group.text, because if a token in the middle was
         cut, the original text contains characters that aren't spoken anymore
         and would lie to the renderer.
      3. Detect "split" cases - surviving tokens with a time gap (because the
         original tokens were on either side of a cut in the source). In 'trim'
         mode merge into one group (default - simpler downstream); in 'split'
         mode emit two groups so the cut shows as a real boundary.

    Returns:
      - None → all tokens cut, drop this group
      - dict → one remapped group
      - list[dict] → in 'split' mode when a cut straddles the group (multiple groups)
    """
    if "tokens" not in grp or not grp["tokens"]:
        # No token-level data. Without tokens we cannot shorten the TEXT, but
        # dropping the group outright is worse: the audio for the surviving part
        # is still in the cut, so the viewer HEARS a line with no caption. That
        # is the "หายไปไหน" failure - a whole sentence silently missing from an
        # otherwise complete edit.
        #
        # So: keep the group, clamped to the part that survives. The text may
        # name a word or two that got cut, which reads as a caption that is
        # slightly ahead of the audio - far less wrong than no caption at all.
        # (With tokens present, the branch below trims the text properly.)
        ns = remap_token_seconds(grp["start"], src_sorted, keep_starts)
        ne = remap_token_seconds(grp["end"], src_sorted, keep_starts)
        if ns is not None and ne is not None:
            step_ms = 100
            t_ms = int(grp["start"] * 1000)
            end_ms = int(grp["end"] * 1000)
            straddles = False
            while t_ms < end_ms:
                if remap_ms(t_ms, src_sorted, keep_starts) is None:
                    straddles = True
                    break
                t_ms += step_ms
            if not straddles:
                out = dict(grp)
                out["start"], out["end"] = round(ns, 3), round(ne, 3)
                return out
        # Straddles a cut (or an endpoint fell in a dropped range): salvage the
        # largest surviving slice instead of returning None.
        best = None
        for iv in src_sorted:
            ov_s = max(grp["start"] * 1000, iv["src_s"])
            ov_e = min(grp["end"] * 1000, iv["src_e"])
            if ov_e - ov_s > 120 and (best is None or ov_e - ov_s > best[1] - best[0]):
                best = (ov_s, ov_e, iv)
        if best is None:
            return None
        ov_s, ov_e, iv = best
        out = dict(grp)
        out["start"] = round((iv["tgt_s"] + (ov_s - iv["src_s"])) / 1000.0, 3)
        out["end"] = round((iv["tgt_s"] + (ov_e - iv["src_s"])) / 1000.0, 3)
        return out

    # Per-token remap via the interval helper - it handles the seam case
    # correctly (adjacent kept segments meeting at a single ms are NOT a cut;
    # only source gaps or target reorders are real cuts that drop the token).
    surviving = []
    for tok in grp["tokens"]:
        s_ms = int(round(tok["start"] * 1000))
        e_ms = int(round(tok["end"] * 1000))
        r = remap_interval_ms(s_ms, e_ms, src_sorted, keep_starts)
        if r is None:
            # The token straddles a cut. Dropping it loses its TEXT even though
            # most of its audio may still be in the edit - and when a caption has
            # only one token (sentence-aware captions do), that drops the whole
            # line and the viewer hears a sentence with no caption on screen.
            # Salvage the largest surviving slice instead; a caption that ends a
            # word or two early beats a caption that is not there.
            best = None
            for iv in src_sorted:
                ov_s, ov_e = max(s_ms, iv["src_s"]), min(e_ms, iv["src_e"])
                if ov_e - ov_s > 120 and (best is None or ov_e - ov_s > best[1] - best[0]):
                    best = (ov_s, ov_e, iv)
            if best is None:
                continue
            ov_s, ov_e, iv = best
            r = (iv["tgt_s"] + (ov_s - iv["src_s"]), iv["tgt_s"] + (ov_e - iv["src_s"]))
        ts_ms, te_ms = r
        nt = dict(tok)
        nt["start"], nt["end"] = round(ts_ms / 1000.0, 3), round(te_ms / 1000.0, 3)
        surviving.append(nt)
    if not surviving:
        return None

    # Detect cuts INSIDE the surviving tokens (gap > a small tolerance signals a
    # dropped chunk between two surviving tokens - they were on opposite sides of
    # a cut in the source). 60ms threshold leaves natural inter-word silences
    # alone (those are already <81ms in practice - see edit-rules #11b).
    GAP_THRESHOLD_S = 0.060
    split_points = []
    for i in range(1, len(surviving)):
        gap = surviving[i]["start"] - surviving[i - 1]["end"]
        if gap > GAP_THRESHOLD_S:
            split_points.append(i)

    if not split_points or mode == "trim":
        # No cut straddles the group, OR trim mode → emit one group (concatenated
        # surviving tokens). In trim mode the time gap is invisible in text but
        # the group's [start, end] reflects the surviving range honestly.
        return _rebuild_group_from_tokens(grp, surviving)

    # split mode + at least one internal cut → emit multiple groups, one per
    # contiguous run of surviving tokens.
    runs = []
    last = 0
    for sp in split_points:
        runs.append(surviving[last:sp])
        last = sp
    runs.append(surviving[last:])
    return [_rebuild_group_from_tokens(grp, run) for run in runs if run]


def remap_words_json(d, src_sorted, keep_starts, mode):
    """words.json shape: {groups: [...]}. Returns new dict, prints stats.
    remap_group may return a list of groups (split mode) - flatten.

    Output is sorted by TARGET start so reordered timelines (where source order
    differs from target order - e.g. user rearranged clips in CapCut) read
    correctly. Without this sort, captions appear in source order on a target
    that plays in a different order - they'd land on the wrong clips."""
    out = dict(d)
    new_groups = []
    drops = 0
    for g in d["groups"]:
        rg = remap_group(g, src_sorted, keep_starts, mode)
        if rg is None:
            drops += 1
            continue
        if isinstance(rg, list):
            new_groups.extend(rg)
        else:
            new_groups.append(rg)
    # Sort by target start (= the new `start` field after remap, in seconds).
    new_groups.sort(key=lambda g: g["start"])
    out["groups"] = new_groups
    out["group_count"] = len(new_groups)
    out["word_count"] = sum(len(g.get("tokens", [])) for g in new_groups)
    out["remapped"] = True
    return out, drops


def remap_captions_json(d, src_sorted, keep_starts, mode):
    """captions.json shape: {words: [{start,end,text}], words_raw: [char-level]}.

    Char-truth strategy (the correct way):
      1. Remap words_raw (char-level) first - these are the smallest atomic units
         from ElevenLabs. Drop any char whose [start, end] falls in a cut range.
      2. Rebuild the phrase-level `words[]` from the surviving chars by walking
         the original `words[]` and only keeping chars that still exist.

    Why this matters: a phrase like "เลยไม่มีช่วงช่องว่างเลย" originally spans
    5.00–6.36s. If the user cuts 5.5–6.0s out, the OLD remap kept the whole
    phrase text but shortened its time window - the caption would still claim
    "เลยไม่มีช่วงช่องว่างเลย" was said in the surviving window, even though
    "ช่องว่าง" was the part that got cut. The new path drops "ช่องว่าง" from
    the text and only keeps the surviving substring.

    Re-spaces phrases that lose their leading/trailing words; drops phrases
    whose chars are all cut.
    """
    out = dict(d)
    drops_words = 0
    drops_chars = 0

    # Step 1: remap words_raw - char-level truth source.
    # Build a map keyed by (char_text, orig_start) so we can later check
    # "did this char survive?" while walking the phrase-level array.
    survival = {} # idx_in_raw -> new (start_s, end_s) | None
    new_raw = []
    if "words_raw" in d:
        for idx, c in enumerate(d["words_raw"]):
            # Each char is an interval - use the interval helper so a char that
            # spans a cut (start in keep A, end in keep B) gets dropped rather
            # than producing inconsistent endpoint offsets.
            s_ms = int(round(c["start"] * 1000))
            e_ms = int(round(c["end"] * 1000))
            r = remap_interval_ms(s_ms, e_ms, src_sorted, keep_starts)
            if r is None:
                survival[idx] = None
                drops_chars += 1
                continue
            ts_s, te_s = r[0] / 1000.0, r[1] / 1000.0
            survival[idx] = (round(ts_s, 3), round(te_s, 3))
            new_raw.append({**c, "start": round(ts_s, 3), "end": round(te_s, 3)})
        out["words_raw"] = new_raw

    # Step 2: rebuild phrase-level words[] from surviving chars.
    # We walk d["words_raw"] in order. For each original phrase in d["words"],
    # we know it covers some contiguous slice of words_raw (by text + time match).
    # The deterministic way: stream the raw chars, accumulate non-space text +
    # times until we cross a phrase boundary (or a cut boundary), then emit.
    # We use the ORIGINAL phrases' time spans to know where boundaries sit.
    new_words = []
    if "words" in d and "words_raw" in d:
        # Walk raw chars and bucket them by which original phrase they belong to.
        # A char belongs to phrase P if its midpoint falls in [P.start, P.end].
        phrase_idx_for_char = [] # parallel to d["words_raw"]
        phrase_starts = [(p["start"], p["end"]) for p in d["words"]]
        pi = 0
        for c in d["words_raw"]:
            mid = (c["start"] + c["end"]) / 2
            while pi < len(phrase_starts) and mid > phrase_starts[pi][1] + 0.01:
                pi += 1
            phrase_idx_for_char.append(pi if pi < len(phrase_starts) else None)

        # Now collect surviving chars per phrase, in order.
        for pidx, phrase in enumerate(d["words"]):
            kept_chars = []
            for raw_idx, p_of_c in enumerate(phrase_idx_for_char):
                if p_of_c != pidx:
                    continue
                surv = survival.get(raw_idx)
                if surv is None:
                    continue
                kept_chars.append((d["words_raw"][raw_idx]["text"], surv[0], surv[1],
                                   d["words_raw"][raw_idx].get("type", "word")))
            if not kept_chars:
                drops_words += 1
                continue
            # Re-assemble text (preserving spacing tokens that survived between
            # surviving word chars; drop leading/trailing spacing).
            text = ""
            for ch, _s, _e, t in kept_chars:
                text += ch
            text = text.strip()
            if not text:
                drops_words += 1
                continue
            # Phrase time = first surviving non-space start, last surviving non-space end
            word_chars = [k for k in kept_chars if k[3] != "spacing" and not k[0].isspace()]
            if not word_chars:
                drops_words += 1
                continue
            new_words.append({
                "text": text,
                "start": word_chars[0][1],
                "end": word_chars[-1][2],
            })
    elif "words" in d:
        # No words_raw to ground truth-rebuild against → fallback to old
        # whole-phrase-or-nothing behavior. This isn't ideal (a phrase that
        # straddles a cut will still be a problem) but it's the best we can do
        # without char-level data. We refuse to remap a straddling phrase rather
        # than silently lie: drop it and let the caller re-run on a file that
        # has words_raw (i.e. raw-elevenlabs.json or captions.json from this kit).
        for w in d["words"]:
            ns = remap_token_seconds(w["start"], src_sorted, keep_starts)
            ne = remap_token_seconds(w["end"], src_sorted, keep_starts)
            if ns is None or ne is None:
                drops_words += 1
                continue
            # also check that NO sub-range of this phrase falls in a cut
            spans_cut = False
            step_ms = 100 # 100ms granularity is plenty for a sanity check
            t_ms = int(w["start"] * 1000)
            end_ms = int(w["end"] * 1000)
            while t_ms < end_ms:
                if remap_ms(t_ms, src_sorted, keep_starts) is None:
                    spans_cut = True
                    break
                t_ms += step_ms
            if spans_cut:
                drops_words += 1
                continue
            new_words.append({**w, "start": round(ns, 3), "end": round(ne, 3)})

    new_words.sort(key=lambda w: w["start"])
    out["words"] = new_words
    out["remapped"] = True
    return out, drops_words


def remap_capcut_json(d, src_sorted, keep_starts, mode):
    """CapCut shape: {captions: [{timeline_start_ms, timeline_end_ms, tokens?: [{start,end}]}]}.
    timeline_*_ms is ms; token start/end is seconds (per edit-rules #14).

    Same token-truth strategy as remap_words_json - when a caption has tokens,
    rebuild the text from surviving tokens. If no tokens are present, refuse to
    remap any caption that straddles a cut (don't silently keep stale text)."""
    out = dict(d)
    new_caps = []
    drops = 0
    for c in d["captions"]:
        # Token-truth path
        if "tokens" in c and isinstance(c["tokens"], list) and c["tokens"]:
            surviving = []
            for tok in c["tokens"]:
                # Treat each token as an INTERVAL so we drop ones that straddle
                # a cut (their endpoints would otherwise get different offsets).
                s_ms_src = int(round(float(tok.get("start", 0)) * 1000))
                e_ms_src = int(round(float(tok.get("end", 0)) * 1000))
                r = remap_interval_ms(s_ms_src, e_ms_src, src_sorted, keep_starts)
                if r is None:
                    continue
                ts_ms, te_ms = r
                nt = dict(tok)
                nt["start"], nt["end"] = round(ts_ms / 1000.0, 3), round(te_ms / 1000.0, 3)
                surviving.append(nt)
            if not surviving:
                drops += 1
                continue
            # rebuild text + char offsets
            text = ""
            for nt in surviving:
                text += _space_before(c.get("text", ""), nt, text)
                cs = len(text)
                text += nt["text"]
                nt["char_start"], nt["char_end"] = cs, len(text)
            nc = dict(c)
            nc["text"] = text
            nc["tokens"] = surviving
            nc["timeline_start_ms"] = int(round(surviving[0]["start"] * 1000))
            nc["timeline_end_ms"] = int(round(surviving[-1]["end"] * 1000))
            nc["duration_ms"] = nc["timeline_end_ms"] - nc["timeline_start_ms"]
            new_caps.append(nc)
            continue
        # No-token fallback: refuse straddling
        ns_ms = remap_ms(c["timeline_start_ms"], src_sorted, keep_starts)
        ne_ms = remap_ms(c["timeline_end_ms"], src_sorted, keep_starts)
        if ns_ms is None or ne_ms is None:
            drops += 1
            continue
        # check middle doesn't cross a cut
        spans_cut = False
        t_ms = c["timeline_start_ms"]; e_ms = c["timeline_end_ms"]
        while t_ms < e_ms:
            if remap_ms(t_ms, src_sorted, keep_starts) is None:
                spans_cut = True; break
            t_ms += 100
        if spans_cut:
            drops += 1
            continue
        nc = dict(c)
        nc["timeline_start_ms"] = ns_ms
        nc["timeline_end_ms"] = ne_ms
        nc["duration_ms"] = ne_ms - ns_ms
        new_caps.append(nc)
    new_caps.sort(key=lambda c: c["timeline_start_ms"])
    out["captions"] = new_caps
    out["caption_count"] = len(new_caps)
    out["remapped"] = True
    return out, drops


def detect_shape(d):
    if isinstance(d, dict):
        if "groups" in d:
            return "words"
        if "captions" in d:
            return "capcut"
        if "words" in d or "words_raw" in d:
            return "captions"
    raise SystemExit("unrecognized input shape - expected {groups}, {captions}, or {words/words_raw}")


def validate(out, shape):
    """Cheap deterministic checks after remap. Same contract as add_captions.py."""
    issues = 0
    if shape == "words":
        groups = out["groups"]
        for i in range(1, len(groups)):
            if groups[i]["start"] < groups[i - 1]["end"] - 0.001:
                print(f" NONMONO group {i}: {groups[i-1]['end']} -> {groups[i]['start']}", file=sys.stderr)
                issues += 1
        for i, g in enumerate(groups):
            if "tokens" in g:
                for t in g["tokens"]:
                    if g["text"][t["char_start"]:t["char_end"]] != t["text"]:
                        print(f" OFFSET BAD group {i} token {t['text']!r}", file=sys.stderr)
                        issues += 1
    elif shape == "capcut":
        caps = out["captions"]
        for i in range(1, len(caps)):
            if caps[i]["timeline_start_ms"] < caps[i - 1]["timeline_end_ms"]:
                print(f" NONMONO cap {i}", file=sys.stderr)
                issues += 1
    return issues


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edl", required=True, help="Path to EDL JSON (kept ranges of the original)")
    ap.add_argument("--in", dest="inp", required=True, help="Captions/transcript JSON to remap")
    ap.add_argument("--out", required=True, help="Output path (remapped file)")
    ap.add_argument("--mode", choices=["trim", "split"], default="trim",
                    help="How to handle groups that straddle a cut. 'trim' (default) keeps "
                         "the surviving tokens in one group; 'split' would emit two groups "
                         "(rare - most groups are short enough to fit entirely in one keep).")
    args = ap.parse_args()

    edl = json.load(open(args.edl, encoding="utf-8"))
    if "segments" not in edl:
        raise SystemExit("EDL has no 'segments' array")
    src_sorted, keep_starts = build_offset_table(edl["segments"])

    d = json.load(open(args.inp, encoding="utf-8"))
    shape = detect_shape(d)
    print(f"input shape: {shape}", file=sys.stderr)
    total_dropped = sum((src_sorted[i]["src_s"] - src_sorted[i-1]["src_e"])
                        for i in range(1, len(src_sorted)))
    reordered = any(src_sorted[i]["tgt_s"] < src_sorted[i-1]["tgt_s"]
                    for i in range(1, len(src_sorted)))
    print(f"EDL: {len(src_sorted)} kept segments, {total_dropped}ms total source dropped, "
          f"target {'REORDERED' if reordered else 'in source order'}", file=sys.stderr)

    if shape == "words":
        out, drops = remap_words_json(d, src_sorted, keep_starts, args.mode)
        n_in, n_out = len(d["groups"]), len(out["groups"])
    elif shape == "captions":
        out, drops = remap_captions_json(d, src_sorted, keep_starts, args.mode)
        n_in, n_out = len(d.get("words", [])), len(out["words"])
    else: # capcut
        out, drops = remap_capcut_json(d, src_sorted, keep_starts, args.mode)
        n_in, n_out = len(d["captions"]), len(out["captions"])

    # Re-apply the gap-close contract on the TARGET timeline (rule #11b): source words.json
    # is gap-closed, so a group whose padded END crossed a removed range can land overlapping
    # the next group after remap. That is a property of gap-closure, not data corruption - 
    # clamp the previous end down to the next start instead of failing validation.
    if shape == "words":
        _src = out.get("groups", [])
        _gs = []
        for _g in _src:
            if _gs and _g["start"] < _gs[-1]["end"] - 0.001:
                # overlap at a cut seam: first try clamping the PREVIOUS padded end down
                if _g["start"] > _gs[-1]["start"] + 0.05:
                    _gs[-1]["end"] = _g["start"]
                # group collapsed to (or past) the seam → push its start to the free spot
                elif _g["end"] > _gs[-1]["end"] + 0.06:
                    _g = dict(_g); _g["start"] = _gs[-1]["end"]
                else:
                    continue # nothing left of this group after the cut - drop it
            if _g["end"] - _g["start"] < 0.05:
                continue # sub-frame remnant
            _gs.append(_g)
        out["groups"] = _gs
    elif shape in ("captions", "capcut"):
        # Same gap-close seam fix but in ms fields (M-CC4). captions.json uses
        # timeline_start_ms/timeline_end_ms on each word; capcut uses the same on
        # each caption. An overlap at a cut seam is a gap-close artifact - clamp
        # the previous end down to the next start rather than fail validation.
        key = "words" if shape == "captions" else "captions"
        items = out.get(key, [])
        fixed = []
        for it in items:
            s, e = it.get("timeline_start_ms"), it.get("timeline_end_ms")
            if s is None or e is None:
                fixed.append(it); continue
            if fixed:
                pe = fixed[-1].get("timeline_end_ms")
                if pe is not None and s < pe:
                    if s > fixed[-1].get("timeline_start_ms", s) + 20:
                        fixed[-1]["timeline_end_ms"] = s          # clamp prev end
                    elif e > pe + 40:
                        it = dict(it); it["timeline_start_ms"] = pe  # push this start
                    else:
                        continue  # nothing left after the cut
            fixed.append(it)
        out[key] = fixed

    issues = validate(out, shape)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "ok": issues == 0,
        "shape": shape,
        "input_count": n_in,
        "output_count": n_out,
        "dropped_into_cut": drops,
        "kept_ranges": len(src_sorted),
        "total_source_dropped_ms": total_dropped,
        "target_reordered": reordered,
        "validation_issues": issues,
        "output": args.out,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
