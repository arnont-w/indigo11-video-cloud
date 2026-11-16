#!/usr/bin/env python3
"""qc_edit.py - one command that answers "is this edit shippable?"

Every defect this checks was caught by the buyer watching playback, one at a time,
after the agent had already reported success. Each round cost a screenshot, an
explanation, and a hand-written Python probe. This runs all of them at once and
prints a verdict, so the agent can self-check BEFORE handing the edit over.

Checks (each maps to a defect found on a real project):

  cut_midword      a boundary inside a phrase with speech continuing past it -
                   "คลุก" was split into "คลุ|ก" and the audio jumped
  noise_only       a kept range with sound but no words - VAD hears a bag rustle
                   as speech when the STT stretches a character over the pause
  timeline_gap     a hole between segments - dead air mid-edit
  seam_in_speech   a join landing inside unbroken speech - the voice audibly skips
  caption_gap      a stretch of speech with no caption on screen
  caption_missing  a spoken character that appears in no caption
  caption_short    a caption too brief to read (usually a truncated one)
  caption_orphan   a caption that starts with a sentence-ending particle
                   ("ครับบ้านก็ไม่") - the particle was torn off the line before
  caption_dup      the same caption text twice in a row - an uncut stutter
                   (a RESTART spanning two captions is NOT caught - see the note in
                   check_captions; four attempts failed and one is worse than none)
  canvas_ratio     canvas orientation disagreeing with the footage - vertical
                   video in a 1920x1080 project renders pillarboxed
  beat_missing     the product name or the CTA is absent from the finished edit
  brand_split      a verified proper noun broken across two captions
  brand_variants   a proper noun on screen in a spelling other than the verified one
  brand_unchecked  post-process recorded no verified name at all (warn)
  speed_unmapped   the host could not map some sped-up clips into the EDL, so any
                   caption finding over that stretch is unproven (warn - audit A #5)
  visual_unverified  the deciding agent could not SEE the frames (no image support),
                   so visual judgments are unproven (warn - audit A #7)
  preserved_tracks the last op changed something it never declared - the buyer's
                   own tracks/materials are not where they left them
  preserved_unchecked  there is no op snapshot to compare against, so "did the kit
                   touch anything else" could not be answered at all (warn)

Voice-over-driven edits (EDL written by vo_assemble.py, "master_clock": "vo"):
  vo_missing       the voice-over is not on an audio track at 0 for its full length
  vo_uncovered     the picture ends before the voice-over does (black under speech)
  vo_overrun       the picture runs on past the voice-over (warn)
  footage_audio    a footage clip still plays its own sound under the VO (warn)
  track_overlap    two segments overlap on one audio track (CapCut cannot play that)

The last three are "the check could not run", NOT "the edit is wrong" (rule #41).
They are warns on purpose: an agent told to iterate until PASS treats a FAIL as
something to fix, and fixing an imaginary defect damages a correct edit.

Usage:
  python3 qc_edit.py --project <name>                  # read the live CapCut draft
  python3 qc_edit.py --edl <edl.json> --stage <stage.json> [--words <words.json>]
  python3 qc_edit.py ... --json                        # machine-readable only

Exit code is 0 when nothing failed, 1 when anything did - so it can gate a script.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import (  # noqa: E402
    SKILL_DIR, KIT_ROOT, draft_state_path, resolve_project,
)
from _preserved import check_preserved  # noqa: E402
from _word_edges import at_word_edge, in_silence  # noqa: E402

MAX_CHAR_S = 0.30        # above this a character is padding, not speech (rule #36)
MIN_CAPTION_S = 0.45     # shorter than this cannot be read
GAP_TOLERANCE_S = 0.40   # caption/timeline gap worth reporting
# Proper nouns to check are DATA, never code. They come from the transcript's
# post-process pass, which writes what it verified against the packaging on camera
# (rule #38) into words.json as:
#     "proper_nouns": [{"canonical": "...", "variants": [...], "verified": true}]
# Hard-coding a product name here shipped one buyer's brand inside every SKU and made
# the check dead for everyone else - the kit is sold to many buyers cutting many
# products.
PROPER_NOUN_KEY = "proper_nouns"

def real_chars(transcript: dict) -> list:
    """Characters that were actually SPOKEN, at a believable duration.

    Excludes audio-event tokens ("[เสียงลม]") and characters the STT stretched over
    trailing silence - those cover pauses, so treating them as speech is what let a
    rustle survive as its own segment.
    """
    out = []
    for c in transcript.get("words_raw") or []:
        t = (c.get("text") or "").strip()
        if not t or t.startswith("["):
            continue
        if c.get("start") is None or c.get("end") is None:
            continue
        if (c["end"] - c["start"]) > MAX_CHAR_S:
            continue
        out.append(c)
    return out


def check_cuts(edl: dict, stage: dict) -> list:
    issues = []
    segs = sorted(edl.get("segments", []), key=lambda s: s["timeline_start_ms"])

    prev_end = 0
    for s in segs:
        gap = s["timeline_start_ms"] - prev_end
        if gap > GAP_TOLERANCE_S * 1000:
            issues.append({
                "check": "timeline_gap", "severity": "fail",
                "at_s": round(prev_end / 1000, 2),
                "detail": f"{gap/1000:.2f}s hole before {s['timeline_start_ms']/1000:.2f}s",
            })
        prev_end = max(prev_end, s["timeline_end_ms"])

    # A seam that lands in the MIDDLE of continuous speech is an audible stutter: the
    # sentence was being spoken in one breath and the edit jumps inside it. Measured
    # case: segments ended 7.10 and resumed 7.24 while VAD reported unbroken speech
    # 2.40-8.70, so the listener hears the voice skip. Removing 0.14s buys nothing -
    # when two kept ranges are adjacent inside one speech run, they should be one
    # range. Cutting between speech runs (a real pause) is fine and expected.
    per_clip = {p["clip_index"]: p for p in stage.get("per_clip", [])}
    vad_cache: dict = {}
    for i in range(len(segs) - 1):
        cur, nxt = segs[i], segs[i + 1]
        if cur["clip_index"] != nxt["clip_index"]:
            continue
        per = per_clip.get(cur["clip_index"])
        if not per or not per.get("vad"):
            continue
        if cur["clip_index"] not in vad_cache:
            try:
                vad_cache[cur["clip_index"]] = json.load(open(per["vad"], encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                vad_cache[cur["clip_index"]] = []
        a, b = cur["source_end_ms"] / 1000, nxt["source_start_ms"] / 1000
        if b - a > 0.35:
            continue  # a real pause was removed - that is the point of the edit
        run = next((r for r in vad_cache[cur["clip_index"]]
                    if r["start"] + 0.05 < a and b < r["end"] - 0.05), None)
        if run is not None:
            issues.append({
                "check": "seam_in_speech", "severity": "fail",
                "at_s": round(cur["timeline_end_ms"] / 1000, 2),
                "detail": f"joins {a:.2f}->{b:.2f}s inside unbroken speech "
                          f"({run['start']:.2f}-{run['end']:.2f}) - only {(b-a)*1000:.0f}ms "
                          f"removed; merge the two ranges instead",
            })

    cache: dict = {}
    for s in segs:
        per = per_clip.get(s["clip_index"])
        if not per:
            continue
        if s["clip_index"] not in cache:
            try:
                cache[s["clip_index"]] = json.load(open(per["transcript"], encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache[s["clip_index"]] = {}
        tr = cache[s["clip_index"]]
        chars = real_chars(tr)
        a, b = s["source_start_ms"] / 1000, s["source_end_ms"] / 1000

        if chars and not s.get("visual_only") and not s.get("holds_visual"):
            if not any(c["start"] < b and c["end"] > a for c in chars):
                issues.append({
                    "check": "noise_only", "severity": "fail",
                    "at_s": round(s["timeline_start_ms"] / 1000, 2),
                    "detail": f"source {a:.2f}-{b:.2f}s has sound but no words",
                })

        # A boundary is bad when speech continues right past it inside the same phrase.
        for edge, t in (("start", a), ("end", b)):
            inside = next((p for p in tr.get("words", [])
                           if p["start"] < t < p["end"]), None)
            if not inside:
                continue
            if any(abs(t - p[k]) < 0.25 for p in tr.get("words", []) for k in ("start", "end")):
                continue
            # Same rule as compute_edl, from the same helper - they used to disagree,
            # which meant a correct cut could fail its own QC.
            verdict = at_word_edge(tr, t, inside["start"], inside["end"])
            if verdict is True:
                continue
            if in_silence(tr, t):
                continue    # a pause the STT wrote no space for is a boundary, not a chop
            if verdict is None:
                # Nothing to segment (a provider that returns no character stream) or
                # no segmenter installed. The cut may be perfect; we cannot tell, and
                # the "speech continues past it" evidence below needs the same missing
                # characters. Reporting this as cut_midword would invent a defect, and
                # since agents iterate until PASS they would then undo a correct edit -
                # the exact failure this helper exists to prevent. Staying silent is
                # the other wrong answer: the boundary really is unchecked, and a
                # buyer should learn that from the report rather than from playback.
                why = ("no character-level timing in the transcript"
                       if not (tr.get("words_raw") or []) else
                       "no Thai word segmenter installed (pip3 install nlpo3 pythainlp)")
                issues.append({
                    "check": "cut_unverified", "severity": "warn",
                    "at_s": round(s["timeline_start_ms"] / 1000, 2),
                    "detail": f"{edge} {t:.2f}s sits inside phrase "
                              f"{inside.get('text', '')!r} - cannot confirm it is a "
                              f"word boundary: {why}",
                })
                continue
            if any(c["start"] > t + 0.05 and c["start"] < inside["end"] - 0.05
                   for c in chars):
                near = "".join(c["text"] for c in chars if abs(c["start"] - t) < 0.4)
                issues.append({
                    "check": "cut_midword", "severity": "fail",
                    "at_s": round(s["timeline_start_ms"] / 1000, 2),
                    "detail": f"{edge} {t:.2f}s cuts through {near!r}",
                })
    return issues


def check_captions(words: dict, edl: dict, stage: dict) -> list:
    issues = []
    groups = sorted(words.get("groups", []), key=lambda g: g["start"])
    if not groups:
        return [{"check": "caption_missing", "severity": "fail",
                 "at_s": 0, "detail": "no captions at all"}]

    # N-word captions are short by design: two words take about 0.4 s to say. Holding
    # them to the sentence-caption bar flagged 301 of 613 two-word captions; the floor
    # for those is the kit's own 200 ms (rule #11).
    n_words = words.get("words_per_group")
    min_caption_s = MIN_CAPTION_S
    if isinstance(n_words, int) and n_words <= 4:
        min_caption_s = 0.10 if n_words == 1 else 0.20   # one spoken word is ~0.2 s
    for i, g in enumerate(groups):
        # On-screen time, not spoken time: add_captions.py holds a caption until the
        # next one starts (gap-close, rule #11b), and a remapped file is not closed yet.
        nxt = groups[i + 1]["start"] if i + 1 < len(groups) else g["end"]
        dur = max(g["end"], nxt) - g["start"]
        if dur < min_caption_s - 1e-3:
            issues.append({"check": "caption_short", "severity": "warn",
                           "at_s": round(g["start"], 2),
                           "detail": f"{dur:.2f}s: {g['text']!r}"})
        for p in ("ครับ", "นะครับ", "ค่ะ", "นะคะ"):
            if g["text"].startswith(p):
                issues.append({"check": "caption_orphan", "severity": "fail",
                               "at_s": round(g["start"], 2),
                               "detail": f"starts with {p!r}: {g['text']!r}"})
                break
        if i and g["text"] == groups[i - 1]["text"]:
            issues.append({"check": "caption_dup", "severity": "warn",
                           "at_s": round(g["start"], 2),
                           "detail": f"repeated: {g['text']!r}"})
        if i:
            gap = g["start"] - groups[i - 1]["end"]
            if gap > GAP_TOLERANCE_S:
                issues.append({"check": "caption_gap", "severity": "warn",
                               "at_s": round(groups[i - 1]["end"], 2),
                               "detail": f"{gap:.2f}s with no caption"})

    joined = "".join(g["text"] for g in groups)

    # An audible repeat: the speaker said a run of words twice and both takes survived.
    # This is the defect the buyer kept finding by ear ("ตอนนี้ผมใช้ตัวเหยื่อ ตอนนี้ผมใช้
    # เหยื่อกำจัด", "ข้อดีข้อดี", "แต่ข้อควรระวังนะครับ" twice). caption_dup only sees
    # two IDENTICAL captions, so it misses a repeat that spans a caption break - which
    # is how every one of these got through. Scan the joined text instead.
    # Compare with a PREFIX match, not exact equality: a restart is rarely identical.
    # "ตอนนี้ผมใช้ตัวเหยื่อ / ตอนนี้ผมใช้เหยื่อกำจัด" shares 11 characters and then
    # diverges - an exact-match scan sees nothing, which is how it reached the buyer.
    # A restart repeats itself IMMEDIATELY - the speaker stops and begins the same
    # sentence again. Anything further apart is ordinary speech: "แบบนี้เลย" and
    # "กำจัดหนู" recur naturally across a pitch and must not be flagged. So the second
    # occurrence has to begin exactly where the first run ends, give or take a couple
    # of characters, and the shared opening has to be long enough to be a sentence
    # rather than a common word.
    # NOTE - audible_repeat is NOT implemented, deliberately.
    #
    # The buyer found the same defect three times by ear: the speaker restarts a
    # sentence ("ตอนนี้ผมใช้ตัวเหยื่อ / ตอนนี้ผมใช้เหยื่อกำจัด", "ข้อดีข้อดี",
    # "แต่ข้อควรระวังนะครับ" twice) and both takes survive the cut. `caption_dup` only
    # sees two IDENTICAL captions, so it misses every one of these.
    #
    # Four detection attempts failed here, and the measurements say why: on these four
    # clips a REAL restart sat 20 characters apart, while an innocent recurrence of
    # "แบบนี้เลย" sat 24. Distance cannot separate them, and the looser attempts
    # reported three correct edits as broken - a false FAIL is worse than no check,
    # because an agent looping until PASS will damage a good edit to satisfy it.
    #
    # This needs a real signal, not a threshold: compare the two runs as WORD
    # sequences (nlpo3 is already wired in via _word_edges) and require the second to
    # restate the first's opening words, or diff against the source phrase to see that
    # the speaker abandoned and re-began. Until then the defect is found by ear, and
    # that limit is stated in the handoff rather than papered over with a check that
    # fires on the wrong things.

    # Proper-noun checks run only when post-process recorded what it verified.
    entries = words.get(PROPER_NOUN_KEY) or []
    if not entries:
        issues.append({"check": "brand_unchecked", "severity": "warn", "at_s": 0,
                       "detail": "no proper_nouns[] in words.json - post-process did "
                                 "not record any verified name, so spelling is unchecked "
                                 "(rule #38)"})
    for e in entries:
        canon = e.get("canonical") or ""
        names = [n for n in ([canon] + list(e.get("variants") or [])) if n]
        for name in names:
            for i in range(len(groups) - 1):
                for split in range(3, max(4, len(name) - 2)):
                    if (groups[i]["text"].endswith(name[:split])
                            and groups[i + 1]["text"].startswith(name[split:split + 3])):
                        issues.append({"check": "brand_split", "severity": "fail",
                                       "at_s": round(groups[i]["start"], 2),
                                       "detail": f"{name!r} split across captions"})
                        break
        # Which spellings are actually ON SCREEN. Match longest-first and blank out
        # each hit, so one occurrence is counted under one spelling only: with
        # variants ["Brand X", "X"] a caption "Brand X" is ONE way, not two.
        # And a variant that merely CONTAINS the verified spelling
        # (the name glued to a particle such as นะครับ, which post-process will
        # plausibly record as a variant) is the right spelling, not a different one -
        # the first live run of proper_nouns[] failed a correct cut exactly this way.
        scan = joined
        present = set()
        for name in sorted(names, key=len, reverse=True):
            if name != canon and canon and canon in name:
                continue
            if name in scan:
                present.add(name)
                scan = scan.replace(name, "\u0000" * len(name))
        present = sorted(present)
        if canon and len(present) > 1:
            issues.append({"check": "brand_variants", "severity": "fail", "at_s": 0,
                           "detail": f"{canon!r} appears {len(present)} ways: {present} - "
                                     f"normalise to the verified spelling"})
        elif canon and present and canon not in present:
            issues.append({"check": "brand_variants", "severity": "fail", "at_s": 0,
                           "detail": f"on screen as {present} but the verified spelling "
                                     f"is {canon!r}"})

    # A beat that vanishes entirely. The edit can be free of every mechanical defect
    # and still be unsellable: a bad trim once deleted the product name from a shipped
    # cut, and QC passed it because nothing was malformed - only missing. These two
    # beats are what an affiliate clip is FOR.
    if entries:
        canon = [e.get("canonical") for e in entries if e.get("canonical")]
        if canon and not any(c in joined for c in canon):
            issues.append({"check": "beat_missing", "severity": "fail", "at_s": 0,
                           "detail": f"the product name {canon[0]!r} is never said - "
                                     f"a cut removed it entirely"})
    if not any(k in joined for k in ("ตะกร้า", "สั่งซื้อ", "กดสั่ง", "ทัก", "ลิงก์", "link")):
        issues.append({"check": "beat_missing", "severity": "warn", "at_s": 0,
                       "detail": "no call to action found (ตะกร้า / สั่งซื้อ / ทัก / link) - "
                                 "confirm the clip still asks for the sale"})

    # Every spoken character must reach a caption.
    per_clip = {p["clip_index"]: p for p in stage.get("per_clip", [])}
    spoken = 0
    cache: dict = {}
    for s in edl.get("segments", []):
        per = per_clip.get(s["clip_index"])
        if not per:
            continue
        if s["clip_index"] not in cache:
            try:
                cache[s["clip_index"]] = json.load(open(per["transcript"], encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache[s["clip_index"]] = {}
        a, b = s["source_start_ms"] / 1000, s["source_end_ms"] / 1000
        # Count CHARACTERS, not STT entries. One ElevenLabs entry can carry several Thai
        # characters, because a consonant arrives with its vowel and tone marks attached
        # ("สวัส" is one entry, four characters). Counting entries here while the
        # numerator below counts characters made `coverage` a ratio between two different
        # units: on a correct caption it measured 1.5, so the 0.9 threshold could only
        # ever fire on text far shorter than the speech - a caption missing a THIRD of the
        # line still scored above 1.0 and passed.
        spoken += sum(len((c.get("text") or "").strip())
                      for c in real_chars(cache[s["clip_index"]])
                      if c["start"] >= a - 0.02 and c["end"] <= b + 0.02)
    if spoken:
        coverage = len(joined.replace(" ", "")) / spoken
        if coverage < 0.9:
            # A host that could not map every clip has an INCOMPLETE picture of what is
            # on the timeline, so low coverage may be its own blind spot rather than a
            # missing caption. premiere_to_edl drops sped-up clips whose source range
            # cannot be derived (it refuses to fabricate one); the words under them are
            # really on screen, so failing here would report a defect that is not there
            # and send the agent off to "fix" a correct edit (audit A risk 5).
            dropped = int(edl.get("dropped_speed_segments") or 0)
            if dropped:
                issues.append({
                    "check": "caption_missing", "severity": "warn", "at_s": 0,
                    "detail": f"captions cover only {coverage*100:.0f}% of spoken text, "
                              f"but {dropped} sped-up clip(s) could not be mapped into "
                              f"the EDL - coverage is unproven, not proven low. Check "
                              f"those clips by eye."})
            else:
                issues.append({"check": "caption_missing", "severity": "fail", "at_s": 0,
                               "detail": f"captions cover only {coverage*100:.0f}% of spoken text"})
    return issues


def check_unverifiable(edl: dict, stage: dict) -> list:
    """What the checks could NOT establish - reported, never assumed either way.

    The kit's contract is that an agent which cannot see must produce the honest flag
    rather than a confident guess (edit-rules.md "Agent-neutral execution"). These surface
    that flag in the QC report so the buyer learns it here rather than on playback.
    """
    issues = []

    dropped = int(edl.get("dropped_speed_segments") or 0)
    if dropped:
        issues.append({
            "check": "speed_unmapped", "severity": "warn", "at_s": 0,
            "detail": f"{dropped} clip(s) with a speed change could not be mapped "
                      f"source<->timeline, so nothing over them is verified "
                      f"(caption and cut findings there are unproven)"})

    # Set by a staging step whose agent had no way to view the frame grids (a Codex
    # build without image support, or grids that were never produced). Visual calls -
    # holding a silent demo, a caption matching what is on screen, a proper noun read
    # off the packaging - rest on having LOOKED (rules #37, #38).
    if stage.get("visual_unverified") or edl.get("visual_unverified"):
        why = (stage.get("visual_unverified_reason")
               or edl.get("visual_unverified_reason")
               or "the deciding agent could not read the frame grids")
        issues.append({
            "check": "visual_unverified", "severity": "warn", "at_s": 0,
            "detail": f"{why} - visual judgments (held silent shots, on-screen text, "
                      f"proper-noun spelling) are unproven for this edit"})
    return issues


def cut_transcript(edl: dict, stage: dict) -> list:
    """What the finished cut actually SAYS, one line per segment.

    Every check in this file measures whether the edit is well-FORMED. None of them
    measure whether it still says what it should, and that gap cost three rounds on
    one clip: a bad trim deleted the PRODUCT NAME and QC reported PASS, because nothing
    was malformed - the product name was simply gone.

    Reading the kept speech back is how a person spots that in one glance:
        ...แว็กซ์สำเร็จรูปแบบนี้เลยของ | ลักษณะซองของเขา...
    The sentence ends on "ของ" and the next begins elsewhere - obvious on sight,
    invisible to a structural check. No LLM, no extra data: it is the same character
    stream the other checks already read.
    """
    per_clip = {p["clip_index"]: p for p in stage.get("per_clip", [])}
    cache: dict = {}
    lines = []
    for s in sorted(edl.get("segments", []), key=lambda x: x["timeline_start_ms"]):
        per = per_clip.get(s["clip_index"])
        if not per:
            continue
        if s["clip_index"] not in cache:
            try:
                cache[s["clip_index"]] = json.load(open(per["transcript"], encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache[s["clip_index"]] = {}
        a, b = s["source_start_ms"] / 1000, s["source_end_ms"] / 1000
        said = "".join(
            c["text"] for c in (cache[s["clip_index"]].get("words_raw") or [])
            if (c.get("text") or "").strip() and not (c.get("text") or "").strip().startswith("[")
            and c.get("start") is not None and c["start"] >= a - 0.02
            and c.get("end") is not None and c["end"] <= b + 0.02
        ).strip()
        lines.append((round(s["timeline_start_ms"] / 1000, 2),
                      round(s["timeline_end_ms"] / 1000, 2), said))
    return lines


def check_canvas(project_dir: Path, edl: dict, stage: dict) -> list:
    try:
        draft = json.loads(draft_state_path(project_dir).read_text(encoding="utf-8"))
    except Exception:
        return []
    cc = draft.get("canvas_config") or {}
    cw, ch = int(cc.get("width") or 0), int(cc.get("height") or 0)
    if not cw or not ch:
        return []
    used = {s["clip_index"] for s in edl.get("segments", [])}
    for c in stage.get("clips", []):
        if c["index"] not in used:
            continue
        sw, sh = int(c.get("width") or 0), int(c.get("height") or 0)
        if not sw or not sh:
            continue

        def orient(w, h):
            return "square" if w == h else ("portrait" if h > w else "landscape")

        if orient(sw, sh) != orient(cw, ch):
            return [{"check": "canvas_ratio", "severity": "fail", "at_s": 0,
                     "detail": f"canvas {cw}x{ch} ({orient(cw,ch)}) vs footage "
                               f"{sw}x{sh} ({orient(sw,sh)}) - will letterbox"}]
        break
    return []


def check_preserved_tracks(project_dir: Path) -> list:
    """preserved_tracks as a qc issue: fail on unplanned_change, warn on skip.

    `skip` is a warn, never silence: an agent reading a clean report would
    otherwise believe the buyer's other tracks were confirmed untouched when
    nothing was actually compared (rule #41).
    """
    try:
        res = check_preserved(project_dir, draft_state_path(project_dir),
                              KIT_ROOT)
    except Exception as ex:
        return [{"check": "preserved_tracks", "severity": "warn", "at_s": 0,
                 "detail": f"ตรวจไม่ได้ (check could not run): {ex}"}]
    if res["status"] == "fail":
        return [{"check": "preserved_tracks", "severity": "fail", "at_s": 0,
                 "detail": res["detail"]}]
    if res["status"] == "skip":
        return [{"check": "preserved_unchecked", "severity": "warn", "at_s": 0,
                 "detail": res["detail"]}]
    return []


def check_vo(edl: dict, project_dir: Path) -> list:
    """Voice-over-driven edit: the VO is the clock, so the picture must cover it."""
    issues = []
    try:
        draft = json.loads(draft_state_path(project_dir).read_text(encoding="utf-8"))
    except Exception as ex:
        return [{"check": "vo_missing", "severity": "warn", "at_s": 0,
                 "detail": f"อ่าน draft ไม่ได้ (could not read the draft): {ex}"}]
    vo = edl.get("vo") or {}
    vo_ms = int(vo.get("duration_ms") or 0)
    mats = {m.get("id"): m for m in (draft.get("materials", {}).get("audios") or [])}
    vo_name = Path(vo.get("path", "")).name
    found = None
    for t in draft.get("tracks") or []:
        if t.get("type") != "audio":
            continue
        for sg in t.get("segments") or []:
            m = mats.get(sg.get("material_id")) or {}
            if Path(m.get("path", "")).name == vo_name or m.get("name") == vo_name:
                found = sg
                break
        if found:
            break
    if not found:
        issues.append({"check": "vo_missing", "severity": "fail", "at_s": 0,
                       "detail": f"ไม่มี {vo_name} บน audio track (voice-over not on the timeline)"})
    else:
        tr = found.get("target_timerange") or {}
        if int(tr.get("start", 0)) > 40_000:
            issues.append({"check": "vo_missing", "severity": "fail", "at_s": int(tr["start"]) / 1e6,
                           "detail": "เสียงพากย์ไม่ได้เริ่มที่ 0 (voice-over does not start at 0)"})
        if vo_ms and int(tr.get("duration", 0)) // 1000 + 100 < vo_ms:
            issues.append({"check": "vo_missing", "severity": "fail", "at_s": 0,
                           "detail": f"เสียงพากย์บน timeline สั้นกว่าไฟล์ ({int(tr.get('duration', 0)) / 1e6:.2f}s "
                                     f"vs {vo_ms / 1000:.2f}s)"})
    main = next((t for t in draft.get("tracks") or [] if t.get("type") == "video"), None)
    end_ms = 0
    for sg in (main or {}).get("segments") or []:
        tr = sg.get("target_timerange") or {}
        end_ms = max(end_ms, (int(tr.get("start", 0)) + int(tr.get("duration", 0))) // 1000)
        if edl.get("footage_audio") == "mute" and float(sg.get("volume", 1.0)) > 0:
            issues.append({"check": "footage_audio", "severity": "warn",
                           "at_s": int(tr.get("start", 0)) / 1e6,
                           "detail": "ฟุตเทจยังมีเสียงอยู่ใต้เสียงพากย์ (footage audio not muted)"})
    if vo_ms and end_ms + 100 < vo_ms:
        issues.append({"check": "vo_uncovered", "severity": "fail", "at_s": round(end_ms / 1000, 2),
                       "detail": f"ภาพหมดที่ {end_ms / 1000:.2f}s แต่เสียงพากย์ยาว {vo_ms / 1000:.2f}s"})
    elif vo_ms and end_ms > vo_ms + 500:
        issues.append({"check": "vo_overrun", "severity": "warn", "at_s": round(vo_ms / 1000, 2),
                       "detail": f"ภาพยาวเกินเสียงพากย์ {(end_ms - vo_ms) / 1000:.2f}s"})
    for t in draft.get("tracks") or []:
        if t.get("type") != "audio":
            continue
        spans = sorted((int(sg["target_timerange"]["start"]),
                        int(sg["target_timerange"]["start"]) + int(sg["target_timerange"]["duration"]))
                       for sg in t.get("segments") or [] if sg.get("target_timerange"))
        for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            if b0 < a1:
                issues.append({"check": "track_overlap", "severity": "fail", "at_s": round(b0 / 1e6, 2),
                               "detail": f"audio track {t.get('id')}: เสียงทับกันบน track เดียว "
                                         f"({a0 / 1e6:.2f}-{a1 / 1e6:.2f}s กับ {b0 / 1e6:.2f}s)"})
                break
    return issues


def vo_transcript(edl: dict) -> list:
    """For a VO edit, 'what the cut says' is the VO line over each shot."""
    return [(round(r["start_ms"] / 1000, 2), round(r["end_ms"] / 1000, 2),
             f"[{r.get('clip')}] {r.get('said', '')}") for r in edl.get("rows", [])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="CapCut project name (reads its live draft)")
    ap.add_argument("--edl", help="EDL json (default: <workspace>/edl.json)")
    ap.add_argument("--stage", help="stage.json (default: <workspace>/stage.json)")
    ap.add_argument("--words", help="captions words.json (default: <workspace>/words.json)")
    ap.add_argument("--workspace", help="work dir; defaults to the project's temp workspace")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args()

    if os.name == "nt":
        for st in (sys.stdout, sys.stderr):
            if hasattr(st, "reconfigure"):
                st.reconfigure(encoding="utf-8", errors="replace")

    ws = Path(args.workspace) if args.workspace else None
    if ws is None and args.project:
        import tempfile
        ws = Path(tempfile.gettempdir()) / "indigo11-capcut-work" / args.project
    edl_p = Path(args.edl) if args.edl else (ws / "edl.json" if ws else None)
    if edl_p and not edl_p.exists() and not args.edl and (ws / "edl-final.json").exists():
        edl_p = ws / "edl-final.json"       # the name SKILL.md tells compute_edl.py to write
    stage_p = Path(args.stage) if args.stage else (ws / "stage.json" if ws else None)
    words_p = Path(args.words) if args.words else (ws / "words.json" if ws else None)
    if (not words_p or not words_p.exists()) and not args.words and args.project:
        # add_captions.py --from-timeline leaves its remapped captions here
        ft = sorted((Path(KIT_ROOT) / "projects" / "_work" / "capcut" / args.project / "from-timeline")
                    .glob("words-*-p[0-9]*.json"))
        merged = sorted((Path(KIT_ROOT) / "projects" / "_work" / "capcut" / args.project
                         / "from-timeline").glob("words-merged-*.json"))
        if len(merged) == 1:
            words_p = merged[0]          # several clips: their per-clip files merged
        elif len(ft) == 1:
            words_p = ft[0]

    have_cut = bool(edl_p and edl_p.exists() and stage_p and stage_p.exists())
    hand_cut = (not have_cut and args.project and words_p and words_p.exists()
                and not args.edl and not args.stage)
    if not have_cut and not hand_cut:
        print(json.dumps({"ok": False, "error": "need --edl and --stage (or --project)"},
                         ensure_ascii=False))
        return 2

    # A project the BUYER cut has no kit EDL or stage: there is no cut of ours to
    # check, but the captions we added still are ours. Check those (and the canvas,
    # and that their tracks survived) rather than refusing to run at all.
    edl = json.loads(edl_p.read_text(encoding="utf-8")) if have_cut else {"segments": []}
    stage = json.loads(stage_p.read_text(encoding="utf-8")) if have_cut else {}

    issues = check_cuts(edl, stage) if have_cut else []
    issues += check_unverifiable(edl, stage) if have_cut else []
    if words_p and words_p.exists():
        issues += check_captions(json.loads(words_p.read_text(encoding="utf-8")), edl, stage)
    if args.project:
        try:
            issues += check_canvas(resolve_project(args.project), edl, stage)
        except Exception:
            pass
        issues += check_preserved_tracks(resolve_project(args.project))
        if edl.get("master_clock") == "vo":
            issues += check_vo(edl, resolve_project(args.project))
    if edl.get("master_clock") == "vo":
        # a row the plan marked "ซับ: -" has no captions on purpose - not a gap to report
        hidden = [(r["start_ms"] / 1000, r["end_ms"] / 1000) for r in edl.get("rows", [])
                  if not r.get("caption", True)]
        issues = [i for i in issues if not (i["check"] == "caption_gap" and any(
            a - 0.5 <= i["at_s"] < b for a, b in hidden))]
    issues += [{"check": "compute_warning", "severity": "fail", "at_s": 0, "detail": w}
               for w in edl.get("warnings", [])]

    fails = [i for i in issues if i["severity"] == "fail"]
    warns = [i for i in issues if i["severity"] == "warn"]
    segs = edl.get("segments", [])
    result = {
        "ok": not fails,
        "op": "qc-edit",
        "duration_s": round(edl.get("total_duration_ms", 0) / 1000, 2),
        "segments": len(segs),
        "fail": len(fails), "warn": len(warns),
        "issues": sorted(issues, key=lambda i: (i["severity"] != "fail", i["at_s"])),
    }
    if hand_cut:
        result["scope"] = ("captions only - the timeline was cut by hand, so there is no kit "
                           "EDL to check the cuts against")
        result["words"] = str(words_p)
    lines = vo_transcript(edl) if edl.get("master_clock") == "vo" else cut_transcript(edl, stage)
    result["transcript"] = [{"start_s": a, "end_s": b, "said": t} for a, b, t in lines]
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not fails else 1

    print(f"{'PASS' if not fails else 'FAIL'}  {result['duration_s']}s  "
          f"{result['segments']} segments  ({len(fails)} fail, {len(warns)} warn)")
    for i in result["issues"]:
        mark = "FAIL" if i["severity"] == "fail" else "warn"
        print(f"  [{mark}] {i['at_s']:>6}s  {i['check']:<16} {i['detail']}")
    if not issues:
        print("  no issues found")
    # Always print what the cut says. A clean structural report is not the same as a
    # complete edit, and this is the only line that shows the difference.
    print("\n  the cut says:")
    for row in result["transcript"]:
        print(f"    {row['start_s']:6.2f}-{row['end_s']:6.2f}  {row['said']}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
