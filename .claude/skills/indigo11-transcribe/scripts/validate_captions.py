#!/usr/bin/env python3
"""validate_captions.py - deep validator for the Thai 2-pass caption pipeline.

The apply-side marker gate (apply_caption.mjs --require-postprocess) only
proves the agent WROTE the pass-1/pass-2 markers.
This validator proves the DATA actually obeys the pipeline invariants, so a
post-process pass that stamped markers but broke tokens can no longer ship
(roadmap PR-P0-01).

Checks (kind-dependent):
  shape        top-level container is groups[]/captions[]/words[] of objects
  markers      pass-1 `post_processed_at`; pass-2 `post_processed=true, pass=2`
               (only with --require-markers)
  windows      caption windows sorted, non-overlapping, duration >= --min-dur-ms
  gap-close    file claiming gap_closed:true has no small residual gaps
               (> 0 and <= --gap-tolerance-ms) between adjacent captions
  tokens       text[char_start:char_end] == token.text (substring invariant),
               char ranges ascending + inside text, token seconds monotonic,
               token seconds inside the caption's window (ms/1000, with epsilon;
               a gap-close-extended end is allowed to exceed the last token)
  units        `*_ms` fields are numbers; bare token start/end are seconds and
               never look like milliseconds (v > 100000 heuristic guard)
  provenance   with --translated: timing fields + markers must survive
               translation untouched vs --source reference (same count, same
               timeline windows)

Failure = exit 1 + JSON {ok:false, errors:[...]}; warnings never fail the gate.
Deterministic, stdlib-only (edit-rules #18) - semantic wording stays with
the agent; this only enforces machine-checkable contracts.

Usage:
  validate_captions.py <words-or-captions.json> [--pass1 captions.json]
      [--require-markers] [--kind auto|pass1|pass2|words]
      [--min-dur-ms 200|100 for word-by-word] [--gap-tolerance-ms 1200]
      [--translated --source <original.json>]
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOKEN_EPS_S = 0.006          # 3-dp rounding on token seconds + ms rounding on windows
SECONDS_SANITY_MAX = 100000  # a bare token start/end larger than this smells like ms


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(json.dumps({"ok": False, "errors": [f"cannot read {path}: {exc}"]},
                                    ensure_ascii=False))
    except json.JSONDecodeError as exc:
        raise SystemExit(json.dumps({"ok": False, "errors": [f"invalid JSON in {path}: {exc}"]},
                                    ensure_ascii=False))


def caption_list(data: Any) -> list[dict[str, Any]] | None:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = next((data[key] for key in ("groups", "captions", "words", "segments")
                      if key in data), None)
    else:
        return None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None
    return items


def window_ms(cap: dict[str, Any]) -> tuple[float, float] | None:
    """Caption window in ms - `timeline_*_ms` wins, bare start/end are seconds."""
    if "timeline_start_ms" in cap or "timeline_end_ms" in cap:
        try:
            return float(cap["timeline_start_ms"]), float(cap["timeline_end_ms"])
        except (KeyError, TypeError, ValueError):
            return None
    try:
        return float(cap["start"]) * 1000.0, float(cap["end"]) * 1000.0
    except (KeyError, TypeError, ValueError):
        return None


def detect_kind(data: Any, caps: list[dict[str, Any]]) -> str:
    if isinstance(data, dict):
        if data.get("post_processed") is True or "captions" in data:
            return "pass2"
        if "post_processed_at" in data and not any("tokens" in cap for cap in caps):
            return "pass1"
    if any("tokens" in cap for cap in caps):
        return "words"
    return "pass1"


def check_tokens(index: int, cap: dict[str, Any], win: tuple[float, float] | None,
                 errors: list[str], require_tokens: bool = False) -> None:
    tokens = cap.get("tokens")
    if tokens is None:
        if require_tokens:
            errors.append(f"caption[{index}]: tokens[] missing - karaoke needs per-token "
                          "timing (even-split fallback drifts off the voice)")
        return
    if not isinstance(tokens, list) or not all(isinstance(token, dict) for token in tokens):
        errors.append(f"caption[{index}]: tokens is not a list of objects")
        return
    text = cap.get("text")
    if not isinstance(text, str):
        errors.append(f"caption[{index}]: has tokens but no text string")
        return
    prev_char_end = 0
    prev_start = None
    for j, token in enumerate(tokens):
        where = f"caption[{index}].tokens[{j}]"
        ttext = token.get("text")
        if not isinstance(ttext, str) or not ttext:
            errors.append(f"{where}: missing/empty text")
            continue
        cs, ce = token.get("char_start"), token.get("char_end")
        if not isinstance(cs, int) or not isinstance(ce, int):
            errors.append(f"{where}: char_start/char_end must be integers")
        elif not (0 <= cs < ce <= len(text)):
            errors.append(f"{where}: char range {cs}..{ce} outside text (len {len(text)})")
        elif cs < prev_char_end:
            errors.append(f"{where}: char range overlaps previous token")
        elif text[cs:ce] != ttext:
            errors.append(f"{where}: substring invariant broken - "
                          f"text[{cs}:{ce}]={text[cs:ce]!r} != token.text={ttext!r}")
        else:
            prev_char_end = ce
        try:
            ts, te = float(token["start"]), float(token["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{where}: missing numeric start/end (seconds) - karaoke would "
                          "fall back to even-split timing (never allowed)")
            continue
        if te < ts:
            errors.append(f"{where}: end {te} before start {ts}")
        if ts > SECONDS_SANITY_MAX or te > SECONDS_SANITY_MAX:
            errors.append(f"{where}: start/end look like milliseconds ({ts}/{te}) - "
                          "bare token times must be SECONDS (unit contract)")
        if prev_start is not None and ts < prev_start:
            errors.append(f"{where}: token timing not monotonic ({ts} < {prev_start})")
        prev_start = ts
        if win is not None:
            lo_s, hi_s = win[0] / 1000.0, win[1] / 1000.0
            if ts < lo_s - TOKEN_EPS_S:
                errors.append(f"{where}: token starts at {ts}s before caption window "
                              f"{lo_s:.3f}s")
            if te > hi_s + TOKEN_EPS_S:
                errors.append(f"{where}: token ends at {te}s after caption window "
                              f"{hi_s:.3f}s")


def validate_file(data: Any, kind: str, min_dur_ms: float, gap_tolerance_ms: float,
                  require_markers: bool, require_tokens: bool = False,
                  ) -> tuple[list[str], list[str], str, int]:
    errors: list[str] = []
    warnings: list[str] = []
    caps = caption_list(data)
    if caps is None:
        return (["top level must be (or contain) a list of caption objects "
                 "(groups[]/captions[]/words[]/segments[])"], warnings, kind, 0)
    if not caps:
        return (["caption list is empty"], warnings, kind, 0)
    if kind == "auto":
        kind = detect_kind(data, caps)

    if require_markers and isinstance(data, dict):
        if kind == "pass1" and not data.get("post_processed_at"):
            errors.append("pass-1 marker missing: post_processed_at")
        if kind == "pass2" and (data.get("post_processed") is not True
                                or int(data.get("pass") or 0) != 2):
            errors.append("pass-2 markers missing: post_processed=true, pass=2")

    gap_closed = bool(isinstance(data, dict) and data.get("gap_closed"))
    prev_win: tuple[float, float] | None = None
    for index, cap in enumerate(caps):
        win = window_ms(cap)
        if win is None:
            errors.append(f"caption[{index}]: no usable timing "
                          "(need timeline_start_ms/timeline_end_ms or start/end)")
            continue
        start_ms, end_ms = win
        if end_ms <= start_ms:
            errors.append(f"caption[{index}]: empty/negative window {start_ms}..{end_ms}ms")
        elif end_ms - start_ms < min_dur_ms:
            errors.append(f"caption[{index}]: duration {end_ms - start_ms:.0f}ms "
                          f"below floor {min_dur_ms:.0f}ms")
        if "duration_ms" in cap:
            try:
                if abs(float(cap["duration_ms"]) - (end_ms - start_ms)) > 1.5:
                    errors.append(f"caption[{index}]: duration_ms={cap['duration_ms']} "
                                  f"disagrees with window ({end_ms - start_ms:.0f}ms)")
            except (TypeError, ValueError):
                errors.append(f"caption[{index}]: duration_ms is not a number")
        if not isinstance(cap.get("text"), str) or not str(cap.get("text")).strip():
            errors.append(f"caption[{index}]: empty text")
        if prev_win is not None:
            if start_ms < prev_win[0]:
                errors.append(f"caption[{index}]: windows not sorted "
                              f"({start_ms} after {prev_win[0]})")
            overlap = prev_win[1] - start_ms
            if overlap > 1.5:
                errors.append(f"caption[{index}]: overlaps previous caption by "
                              f"{overlap:.0f}ms")
            gap = start_ms - prev_win[1]
            if gap_closed and 1.5 < gap <= gap_tolerance_ms:
                errors.append(f"caption[{index}]: file claims gap_closed but a "
                              f"{gap:.0f}ms residual gap remains (blink on screen)")
            elif not gap_closed and 1.5 < gap <= gap_tolerance_ms:
                warnings.append(f"caption[{index}]: {gap:.0f}ms gap - run gap-close "
                                "before rendering or the line blinks")
        prev_win = win
        check_tokens(index, cap, win, errors, require_tokens)
    return errors, warnings, kind, len(caps)


def check_translation_provenance(translated: Any, source: Any) -> list[str]:
    errors: list[str] = []
    t_caps, s_caps = caption_list(translated), caption_list(source)
    if t_caps is None or s_caps is None:
        return ["translated/source must both contain caption lists"]
    if len(t_caps) != len(s_caps):
        errors.append(f"translation changed caption count: {len(s_caps)} -> {len(t_caps)} "
                      "(timestamps are the provenance - counts must match)")
        return errors
    for index, (t, s) in enumerate(zip(t_caps, s_caps)):
        tw, sw = window_ms(t), window_ms(s)
        if tw is None or sw is None:
            errors.append(f"caption[{index}]: missing timing on translated or source")
            continue
        if abs(tw[0] - sw[0]) > 1.5 or abs(tw[1] - sw[1]) > 1.5:
            errors.append(f"caption[{index}]: translation moved the window "
                          f"{sw} -> {tw} (timestamps must survive translation)")
    if isinstance(source, dict) and isinstance(translated, dict):
        if source.get("post_processed") is True and translated.get("post_processed") is not True:
            errors.append("translation dropped the pass-2 provenance markers")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep validator for 2-pass Thai captions")
    parser.add_argument("file", help="words.json / captions.json / pass-2 output to validate")
    parser.add_argument("--pass1", help="Also validate this pass-1 captions.json (markers + shape)")
    parser.add_argument("--kind", choices=["auto", "pass1", "pass2", "words"], default="auto")
    parser.add_argument("--require-markers", action="store_true",
                        help="Fail when the pass-1/pass-2 provenance markers are missing")
    parser.add_argument("--require-tokens", action="store_true",
                        help="Fail when a caption has no tokens[] (karaoke path)")
    parser.add_argument("--min-dur-ms", type=float, default=None,
                        help="Shortest caption allowed. Default 200, or 100 for a word-by-word file "
                             "(words_per_group: 1), where one spoken word is often under 200 ms")
    parser.add_argument("--gap-tolerance-ms", type=float, default=1200.0)
    parser.add_argument("--translated", action="store_true",
                        help="File is a translated track; requires --source")
    parser.add_argument("--source", help="Original-language file for provenance comparison")
    args = parser.parse_args()

    if args.translated and not args.source:
        parser.error("--translated requires --source")

    data = load(Path(args.file).expanduser())
    if args.min_dur_ms is None:
        one_word = isinstance(data, dict) and data.get("words_per_group") == 1
        args.min_dur_ms = 100.0 if one_word else 200.0
    errors, warnings, kind, count = validate_file(
        data, args.kind, args.min_dur_ms, args.gap_tolerance_ms, args.require_markers,
        args.require_tokens)

    pass1_checked = False
    if args.pass1:
        pass1_data = load(Path(args.pass1).expanduser())
        p_errors, p_warnings, _, _ = validate_file(
            pass1_data, "pass1", args.min_dur_ms, args.gap_tolerance_ms, args.require_markers)
        errors += [f"pass1: {e}" for e in p_errors]
        warnings += [f"pass1: {w}" for w in p_warnings]
        pass1_checked = True

    if args.translated:
        errors += check_translation_provenance(data, load(Path(args.source).expanduser()))

    report = {"ok": not errors, "kind": kind, "captions": count,
              "pass1_checked": pass1_checked,
              "errors": errors, "warnings": warnings}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
