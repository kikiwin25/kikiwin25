# ▶️ YouTube Shorts Automation — 100% Google AI

One scheduled GitHub Actions run per day: **Gemini writes a script → Gemini TTS
narrates it → Gemini generates the scenes → ffmpeg renders a vertical Short
with CapCut-style karaoke captions → the video is uploaded to your YouTube
channel automatically.**

No other AI providers, no editor, no manual steps. You only touch `config.yaml`.

```
Gemini 2.5 Flash ──► fresh topic + script + title/description/tags (JSON)
Gemini TTS      ──► narration, sentence by sentence (word-exact timing)
Gemini Images   ──► vertical scene backgrounds (styled-gradient fallback)
ffmpeg + libass ──► 1080×1920 Short with word-by-word yellow captions
YouTube Data API──► auto-upload (or auto-schedule) + playlist + publish log
```

---

## 1 · Setup (one time, ~10 minutes)

### Step 1 — Gemini API key
Your Google AI Pro subscription includes AI Studio access.
Go to **https://aistudio.google.com/apikey** → *Create API key* → copy it.
(The free tier is enough for one Short per day.)

### Step 2 — YouTube API access (one OAuth approval)
1. Open **https://console.cloud.google.com** → create a project (any name).
2. *APIs & Services → Library* → search **YouTube Data API v3** → **Enable**.
   (New projects must also fill the OAuth consent screen — External, add
   yourself as a **test user**; you don't need to submit it for verification.)
3. *APIs & Services → Credentials → Create credentials → OAuth client ID* →
   Application type **Desktop app** → copy the **Client ID** and **Client secret**.
4. On **your own computer** (needs a browser):
   ```bash
   git clone <this repo> && cd <repo>
   pip install -r requirements.txt
   YT_CLIENT_ID="<paste>" YT_CLIENT_SECRET="<paste>" python -m shorts.auth_setup
   ```
   A browser opens → choose your channel's Google account → Allow.
   The script prints **three secrets**: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN`.

> ⚠️ A brand-new Cloud project can take a few minutes (or up to 24h for the
> audit status) before uploads are allowed. This is normal.

### Step 3 — Put the four secrets into GitHub
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | from Step 1 |
| `YT_CLIENT_ID` | from Step 2 |
| `YT_CLIENT_SECRET` | from Step 2 |
| `YT_REFRESH_TOKEN` | from Step 2 |

### Step 4 — Tell it your niche
Edit `config.yaml`:
```yaml
channel:
  niche: "mind-blowing science and psychology facts"   # ← YOUR topic
  language: "en"                                       # or ar, fr, es, de...
```

**Done.** The workflow fires daily at 16:00 UTC (17:00 Marrakesh) and your
Short publishes itself. Run it manually any time: **Actions → YouTube Shorts
automation → Run workflow** (optional topic box, optional dry-run).

---

## 2 · Daily operation

| Want to... | Do this |
|---|---|
| Change niche / language / captions / voice / length | edit `config.yaml`, commit |
| Post right now on a specific topic | Actions → run workflow → type the topic |
| Post without uploading (preview first) | run workflow with `dry_run` ✓, download the `short-video` artifact |
| See everything already posted | `state/published.json` (committed automatically) |
| Stop posting | disable the workflow (Actions → ⋯ → Disable) — nothing else to clean up |

The bot never repeats a topic: every used topic is stored in
`state/used_topics.json` and fed back into the prompt.

---

## 3 · Tuning cheatsheet (`config.yaml`)

| Setting | Meaning |
|---|---|
| `video.target_seconds` | rough narration length (Shorts must stay < 60s) |
| `video.scenes` | number of AI background images (1–4) |
| `tts.voice` | `Puck` (upbeat male), `Kore` (firm female), `Charon` (calm male), `Leda` (bright female)… |
| `captions.font_size` / `margin_v` | caption size / height above the bottom |
| `upload.privacy` | `public`, `unlisted`, `private` or `schedule` (auto-publish after `schedule_offset_minutes`) |
| `music.enabled` + `assets/music.mp3` | optional background music (royalty-free!) |
| `upload.made_for_kids` | YouTube "made for kids" flag |

---

## 4 · Local / manual use (optional)

```bash
cp .env.example .env        # paste GEMINI_API_KEY + the three YT_* values
pip install -r requirements.txt

python -m shorts.doctor            # pre-flight check
python -m shorts.run --dry-run     # generate today's short, no upload
python -m shorts.run               # generate + upload
python -m shorts.run --topic "why the ocean glows at night"
python -m shorts.run --selftest    # render test, no API keys at all
```

Finished videos land in `outputs/<date>_<topic>/final.mp4` together with
`script.json` and `subs.ass` (the captions).

## 5 · Troubleshooting

| Symptom | Fix |
|---|---|
| `quotaExceeded` on upload | YouTube default = 6 uploads/day; you'll self-heal tomorrow, or request more quota in Cloud Console |
| `invalidGrant` after weeks | refresh token expired → re-run `python -m shorts.auth_setup`, update the secret |
| Actions didn't run today | GitHub pauses cron after ~60 days without repo commits; run the workflow manually once |
| Captions missing glyphs (Arabic, etc.) | install a font covering your language and set `captions.font` |
| Image step falls back to gradients | Gemini image quota/availability — the video still renders; try again later or raise `limits.gemini_image_model` availability |

## 6 · Style: Vox-style explainers (active setup)

`config.yaml` is currently set to **Vox-style Arabic explainers**:
- `channel.style: "vox"` — scripts follow the Vox arc: question hook → context →
  twist → insight → "why it matters". Switch to `"facts"` for punchy listicles.
- `channel.language: "ar"` — script, Gemini TTS narration (فصحى) and captions
  all in Arabic. The repo bundles the **Almarai** + **Tajawal** fonts (SIL OFL)
  in `fonts/` so captions render with correct RTL letter shaping out of the box.
  For a Latin-language channel: set `language` back and `captions.font: "DejaVu Sans"`.
- `video.scene_style` — flat editorial Vox illustration look for the backgrounds.

## 7 · Good to know

- **AI-content disclosure**: when upload asks, set "Altered content → Yes" in
  YouTube Studio if your audience can't tell the voice is synthetic — keeps you
  on the right side of YouTube's synthetic-media policy.
- `state/published.json` + `state/used_topics.json` are committed by the bot
  after every run — that's expected.
- Videos also land in the run **artifacts** (7-day retention) even if the
  upload fails.
