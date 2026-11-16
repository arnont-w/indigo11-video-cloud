# SPEC: evidence explainer (Thai health reel, studied with indigo11-watch)

STATUS: Level 2 breakdown, visual + audio, from 6 fps grids. Not yet measured at frame level (easing curves are estimates). Transcript not run; wording taken from caption.txt.

## 1. Identity and format
- 1080x1920, 30 fps, 60.38 s, 1,811 frames. h264 ~2.5 Mbps, AAC 48 kHz stereo.
- One continuous composition (no hard cuts; popscan found 2 "cuts" at 8.03 s and 49.60 s, both are scene fade-throughs).
- Fully animated vector explainer. No footage, no face. Voice-over + music bed + SFX.
- Scene count: 15 scenes, 2-6 s each (median ~3.5 s).

## 2. Shot (scene) table
| # | Time | Pill label | Visual | Narration idea |
|---|---|---|---|---|
| 1 | 0.0-3.1 | ข้ามมื้อเช้า (yellow) + เบาหวาน? (red) | plate (egg+toast), red slash drawn across it | hook question |
| 2 | 3.2-5.8 | - | balance scale: ความเชื่อ (purple) vs ความจริง (teal), tips and settles | "true only half" |
| 3 | 6.0-8.0 | งานวิจัยรวม (purple) | 100-dot grid fills in 3 colours, counter "กว่า ๑๐๐,๐๐๐ คน" | pooled studies |
| 4 | 8.0-13.0 | เบาหวานชนิดที่ ๒ (yellow underline title) | 2 bars (teal eat / red skip) grow, badge +21% | risk +21% |
| 5 | 13.3-19.2 | ยิ่งข้ามบ่อยยิ่งเสี่ยง | line chart, dot draws curve to plateau, highlight band 4-5 days, badge +55% | dose-response |
| 6 | 19.3-24.1 | งานวิจัยแบบสังเกต | magnifier over dots; chips เกี่ยวข้อง ✓ then เป็นสาเหตุ ✗ | observational caveat |
| 7 | 24.4-27.3 | งานวิจัยพันธุกรรม | DNA helix builds, rotates | Mendelian randomisation |
| 8 | 27.5-30.7 | ข้ามมื้อเช้า -> เบาหวาน | two nodes, line grows, red ✗ in the middle, "ไม่พบผลโดยตรง" | no direct effect |
| 9 | 31.0-35.5 | ข้ามมื้อเช้า | flow chart: 2 purple chips (น้ำหนักขึ้น, ไขมันหน้าท้องเพิ่ม) -> red ภาวะดื้ออินซูลิน | mechanism |
| 10 | 35.7-40.0 | ทานหนักตอนดึก (navy) | moon + spinning clock + plate | late heavy eating |
| 11 | 40.3-44.8 | IF ที่วางแผนดี (teal) | clock, ticking hand, ✓ chip "น้ำตาลไม่แย่ลง" | planned IF |
| 12 | 45.0-47.0 | น้ำตาลสะสม (HbA1c) | capsule gauge level drops, chip ลดลง + green arrow | HbA1c improves |
| 13 | 47.0-49.6 | - | plate returns with green ✓ badge, "มื้อเช้าไม่ใช่ตัวการ" | conclusion |
| 14 | 49.7-54.5 | ที่สำคัญคือ | 3 stacked pills: ทานอะไร (teal), ทานเท่าไหร่ (purple), ไม่ทานดึก (red) | takeaway |
| 15 | 54.6-60.4 | ทานยาเบาหวาน | red medical cross, red warning pill "ปรึกษาแพทย์ก่อนข้ามมื้ออาหาร", "น้ำตาลอาจต่ำเกินไป", channel brand line, disclaimer, + ติดตาม button | safety + CTA |

## 3. Story beats (framework: myth-check, "true only half")
Hook (question) 0-3 -> verdict teaser 3-6 -> evidence for (21%, 55%) 6-19 -> caveat (observational) 19-24 -> better evidence (genetic) 24-31 -> mechanism 31-35 -> nuance (late eating, IF) 35-45 -> benefit (HbA1c) 45-47 -> conclusion 47-54 -> safety/CTA 54-60.
New vs a plain myth video: gives both sides, then a reversal ("association is not cause") at ~19 s, then the safety warning before the CTA.

## 4. On-screen text (order)
Per scene: one pill label at top; a faint citation line ("Bi et al., Public Health Nutr 2015", "Ballon et al., J Nutr 2019", "Xia et al., Clin Nutr ESPEN 2023", "Khalafi et al., Diabetes Obes Metab 2024") 0.5 s after the label; a big keyword line in the middle; karaoke caption at the bottom. Final scene adds the channel brand line, a disclaimer line and a "+ ติดตาม" chip.
Numbers are Thai numerals in the visuals (๒, ๑๐๐,๐๐๐, ๒๑%, ๕๕%); Thai ๑ looks like an Arabic 9, so check numerals by eye, not by glyph shape. Visual and caption.txt agree.

## 5. Style fingerprint
- Background: warm cream #F5EFE6 with soft diagonal pale streaks (lilac / mint / cream), faint paper grain and dotted texture. Slow drifting confetti flecks (mint, lilac, yellow, 3-6 px, rotated bars) over the whole video.
- Accent palette (visual estimate, not measured): yellow ~#FFC93C, teal ~#1AA390, purple ~#6C4CE0, orange-red ~#FF4B2B, navy ~#1F2750, ink black outlines.
- Illustration: flat vector, 3-4 px black outlines, rounded shapes, single drop colour, no gradients. Plate, scale, dots, DNA, clock, capsule gauge, magnifier, medical cross.
- Pills: rounded, 2-3 px black outline, white bold Thai text, one colour per topic (yellow = subject, purple = study type, teal = good, red = risk/warning, navy = night).
- Fonts: rounded Thai display sans, bold (Noto Sans Thai / Prompt / Kanit family, not identified). Citation in light weight, ~14 px.
- Caption: bottom third, bold white text with black outline + soft shadow, one word at a time in a yellow-highlight box, key words coloured yellow or red.
- Frame rate: 30 fps smooth (not on twos).

## 6. Motion numbers (estimates from 6 fps; verify with strip.py)
- Scene in: content fades up with ~6-8 frame ease-out; pill label drops/scales in first, illustration 3-5 frames later.
- Scene out: whole scene fades (about 6 frames) and the next one starts at once; the background does not cut.
- Bars: grow 0.5 s with ease-out, +21% badge pops 0.3 s after, dashed baseline appears.
- Line chart: dot travels along the curve over ~3 s, ease-out plateau (the curve flattens like a dose-response).
- Dots grid: 100 dots fill row by row over ~1.2 s with random colour order.
- Clock hands: continuous sweep, ~1 turn per 2 s.
- Check/cross badges: scale in with a small overshoot, 3-4 frames.
- Caption words: each word fades in with a small upward slide, ~3 frames, in step with the voice.

## 7. Camera
None. All elements are fixed-position. Only the background streaks and confetti drift.

## 8. Transitions
Single type: cross-fade through the empty cream background (~0.3 s out, 0.3 s in). No wipes, zooms or flashes. Every scene opens with the pill label.

## 9. Sound map
- Tempo 136 BPM (beat 0.441 s). 139 beats detected, so a music bed runs the whole clip.
- Strong cues cluster on scene starts: 4.12, 6.43, 8.22, 19.93, 22.54, 24.72, 31.37, 34.44, 36.11, 47.88, 49.66 (all within 0.2 s of a scene change).
- 349 percussive hits: pops/ticks on each pill, badge and caption word. Top hits at 0.31, 1.33, 4.09, 6.43, 8.37, 12.13, 15.50, 19.92, 25.14, 30.00, 34.44, 49.66.
- Loudness: voice around -14 to -20 dBFS, music ducked under voice (dips to -25...-32 dBFS in gaps). Last 1.06 s is silence (59.33-60.39).
- Voice: single Thai male-style narrator (the caption.txt closes with "ครับ").

## 10. Pacing numbers
- Scene length: min 2.0 s, median ~3.5 s, max ~6.5 s.
- New visual element every ~0.8 s inside a scene (chip, badge, bar, label).
- Caption: about 2-4 Thai words per phrase, wrapping to a second line at about 18 Thai characters.
- Narration is continuous; the picture leads the voice by about 0.2-0.5 s (pill appears before the word is said).

## 11. What makes it work (rules)
1. One idea per scene, named by a coloured pill at the top.
2. Same cream background and confetti all through, so scene changes feel soft.
3. Colour is meaning: yellow subject, teal good, red risk, purple study type.
4. Every claim carries its citation on screen, small and quiet.
5. Show the number as a shape first (bars, dots, curve), then the badge.
6. Hold the central visual still while only the caption moves.
7. Counter-evidence follows evidence ("observational" then "genetic").
8. Safety warning and disclaimer before the CTA.
9. A single silent beat at the very end.
10. Illustrations are simple and reused (plate appears at open and at conclusion).

## 12. Reusable parts and caveats
- Reusable as a template: pill label + illustration + citation + karaoke caption layout; 15-scene myth-check story shape; palette and confetti background; audio ducking.
- Needs rebuild per topic: illustrations (plate, scale, DNA, clock, capsule gauge, magnifier, medical cross).
- Caveats: colours and easing are estimates; fonts not identified; transcript not machine-checked.
- Do not copy the other creator's music or voice; this is the user's own channel, so brand and wording can be reused freely.
