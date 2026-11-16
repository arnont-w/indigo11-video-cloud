INDIGO11 VIDEO PACK IN THE CLOUD: OVERVIEW

Claude Code makes and edits videos for you on a computer in the cloud. You only need a phone,
a tablet or a web browser. Your raw clips come in through Google Drive, and Claude gives
you a download link for each finished video. Your private GitHub repository holds the
skills, your brand and your rules.
New here? Read CLOUD-QUICK-START.txt first (7 steps), then come back to this page.

-----------------------------------------------------------------
1. WHAT YOU NEED
-----------------------------------------------------------------
- A paid Claude plan that includes Claude Code (Pro or Max).
- A free GitHub account (github.com).
- A free Google Drive account for your raw clips (15 GB, shared with Gmail and Photos).
- An ElevenLabs key (elevenlabs.io, Profile, API keys). Needed for captions, transcripts
  and voice-over. Without it only simple jobs work, such as making the sound level even.
- Optional, free: a Pexels key and a Pixabay key for stock video and photos.
- Optional, paid: a Gemini, OpenAI or xAI key for AI images, AI video and AI music.

-----------------------------------------------------------------
2. WHAT WORKS IN THE CLOUD
-----------------------------------------------------------------
Works:
- Editing your own clip to a finished mp4: cut silence and retakes, burned-in captions,
  even sound level, background music.
- A whole short video from one topic ("make a clip about ...").
- Captions, SRT files, transcripts and text to speech.
- Finding sound effects, music, B-roll, icons and fonts.
- Studying a reference video (summary, breakdown, copy the style).

Does not work:
- Anything inside CapCut Desktop (captions, effects, undo in a CapCut project).
  That needs a PC or Mac and the PC version of this pack.
- YouTube and TikTok links often fail from cloud computers. Put the file on Drive instead.
- Very long videos are slow. Clips of a few minutes work best.

-----------------------------------------------------------------
3. SET-UP (ONCE)
-----------------------------------------------------------------
1. On GitHub, open the pack link you were given. Tap "Use this template", then "Create a new
   repository". Give it a name (for example my-studio), choose Private, tap Create.
   This is your own copy. Nobody else sees it.
2. Open claude.ai/code in a browser (or Code in the Claude app). Connect GitHub when it asks
   and allow access to your new repository.
3. Open the settings of your cloud environment and fill in:
   a) Network access: choose Limited and paste the list from part 11 into "Allowed domains".
   b) Environment variables, one per line, with your own keys:
          ELEVENLABS_API_KEY=your-key
          PEXELS_API_KEY=your-key      (optional)
          PIXABAY_API_KEY=your-key     (optional)
   c) Save. Choose this environment every time you start a session.
4. Choose your repository and type: start
   Claude installs what it needs (1 to 2 minutes) and then asks a few questions about your
   brand (name, colours, tone, call to action). You can skip any question and answer later.
Menu names in Claude and GitHub can change. Look for the nearest name.

-----------------------------------------------------------------
4. EVERY VIDEO
-----------------------------------------------------------------
1. Upload your raw clip to Google Drive. Tap Share, set "General access" to "Anyone with
   the link" and copy the link.
2. Open claude.ai/code, choose your repository, paste the link and say what you want:
   "cut the silences and add 2-word captions"
   "make a clip about three habits for better sleep"   (no raw clip needed)
3. Claude gives you a link to each finished file. Tap it to download, and keep the video on
   your phone or in Drive. If GitHub says "can't show files this big", that is normal: tap
   the download arrow.
4. Delete the raw clip from Drive afterwards (or turn sharing off). This keeps your 15 GB
   free and your clip private.
5. Only when Claude says it changed your brand or rules: tap "Create PR", then "Merge".
   This saves them for the next session.
The cloud computer is removed after each session. Raw clips are never stored on GitHub.
Finished videos are, and they use space there: see part 9.

-----------------------------------------------------------------
5. THE SKILLS (just ask in plain words)
-----------------------------------------------------------------
indigo11-edit          Cut silence and retakes to an mp4. Its CapCut jobs need a PC or Mac.
indigo11-video         A whole short video from one topic.
indigo11-transcribe    Captions and SRT files from speech; text to speech.
indigo11-find-assets   Sound effects, music and B-roll.
indigo11-watch         Study a reference video.

-----------------------------------------------------------------
6. YOUR FOLDERS AND FILES  (yours; Claude fills them for you)
-----------------------------------------------------------------
Google Drive          Your raw clips. You upload them and give Claude the link.
my-rules/             Your own rules. Tell Claude "remember: ..." and it saves the rule here.
                      This is the ONLY place for your changes. Named styles are saved here too.
assets/               Things you keep and reuse: brand, music, sound effects, images, fonts
                      and templates. Your brand is in assets/brand.
projects/             Working space on the cloud computer (raw-videos, results). It is
                      emptied after every session. Only finished files go to GitHub.

-----------------------------------------------------------------
7. SYSTEM FOLDERS AND FILES  (Claude uses them; do NOT change them)
-----------------------------------------------------------------
.claude/              The skills (know-how and scripts) and the cloud rules.
_system/              Helper files, the install guide and the cloud setup script.
CLAUDE.md             Claude's standing instructions for this folder.
.gitignore            Keeps keys and raw clips out of your repository.
Why leave them alone? An update to the pack replaces these files, so your edits would be
lost. Anything personal goes in my-rules/ instead.
Every folder has its own README.txt with one short paragraph.

-----------------------------------------------------------------
8. KEYS, COSTS, PRIVACY AND ASSETS
-----------------------------------------------------------------
- Keys live only in the environment settings. Never paste a key into the chat, never put one
  in a file, and do not share your environment with other people.
- Voice-over and captions use your ElevenLabs credits. AI images, video and music cost money:
  Claude shows the price first and waits for your yes.
- "Anyone with the link" means anyone who has the link can see the clip. Delete the clip or
  turn sharing off when the job is done.
- Search order for any asset: your own files first, then free sources, then drawing or
  generating (price first), then buying last, only with your yes.

-----------------------------------------------------------------
9. GITHUB SPACE
-----------------------------------------------------------------
- Raw clips never go to GitHub. Finished videos do, and each one uses about 20 MB for good:
  deleting a file later does not give the space back.
- GitHub advises staying under 1 GB (about 50 videos) and never above 5 GB (about 250).
- Download every finished video soon and keep it on your phone or in Drive.
- Claude tells you when your repository gets big. Then make a fresh copy:
  1. In the old repository, ask Claude: "show me my brand and my rules as text". Copy it.
  2. Open the pack link and tap "Use this template" again to make a new repository.
  3. In the new repository type "start", paste the text and say "save this as my brand
     and rules". You can then delete the old repository on GitHub.

-----------------------------------------------------------------
10. IF SOMETHING GOES WRONG
-----------------------------------------------------------------
Claude cannot open the link  Check the clip is shared as "Anyone with the link".
Everything is blocked, or    You started the session in another environment. Start a new
the key is empty after       session and choose the environment you set up.
set-up
The repository gets big      Make a fresh copy: see part 9.
A web address is blocked     Add it to "Allowed domains", save, start a new session.
                             Still blocked? Set Network access to Full.
"Key is empty"               Add the key under Environment variables, save, start a new session.
Brand or rules are gone      The last change was not merged. On GitHub open "Pull requests",
                             open the newest one and tap "Merge".
The session stopped          Cloud computers pause when idle. Open the session again and say
                             "continue". Unfinished videos may need to be made again.

-----------------------------------------------------------------
11. ALLOWED DOMAINS (paste all lines)
-----------------------------------------------------------------
api.elevenlabs.io
drive.google.com
docs.google.com
drive.usercontent.google.com
*.googleusercontent.com
api.pexels.com
pexels.com
*.pexels.com
pixabay.com
*.pixabay.com
mixkit.co
*.mixkit.co
coverr.co
*.coverr.co
incompetech.com
ccmixter.org
*.ccmixter.org
www.myinstants.com
api.fontsource.org
cdn.jsdelivr.net
api.iconify.design
generativelanguage.googleapis.com
api.openai.com
api.x.ai
openrouter.ai
archive.ubuntu.com
security.ubuntu.com
