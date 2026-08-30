# C-ECM — 3-Minute Demo Video Script

**Hard rules from the hackathon guide (do not violate these):**
- **3:00 maximum.** Judges will not watch past 3:00 — anything after is wasted.
- **At least 90 seconds must be live, on-screen product**, not talking-head or slides.
- **You must clearly demonstrate how IBM Bob was used** — narrated, not just implied.
- Narrate everything. Judges see a lot of silent screen-shares — narration is what separates a demo from a screen recording.

**Target runtime: 2:55** (5-second safety buffer before the hard 3:00 cutoff).

---

## At-a-glance timing

| Beat | Time | Duration | What's on screen |
|---|---|---|---|
| 1. Cold open / hook | 0:00–0:12 | 12s | 3 quick on-screen questions, no narration yet |
| 2. Problem | 0:12–0:42 | 30s | Fragmented-systems diagram / montage |
| 3. Transition | 0:42–0:49 | 7s | C-ECM title card |
| 4. **Live demo** | 0:49–2:19 | **90s** | The actual running app (see shot list) |
| 5. Built with IBM Bob | 2:19–2:47 | 28s | Bob agent-mode session recording/screenshots |
| 6. Close | 2:47–2:55 | 8s | End card, one-line differentiator |

---

## Full narration script (read this straight through once to check your pace)

> When an auditor asks "who approved this release, and where's the proof?" — for most engineering teams, that question means hours of digging.
>
> Release runbooks, change approvals, and compliance evidence end up scattered across FileNet, SharePoint, a shared drive, sometimes even a mainframe — with no shared audit trail between any of them. Every ECM vendor's answer is "migrate everything to us." That's expensive, slow, and it doesn't help with the systems you can't move.
>
> So we built C-ECM: one governance and audit layer that sits over every system you already have — no migration required.
>
> One search box reaches every backend at once — FileNet, Google Drive, S3, IBM i — results grouped by system, in seconds, instead of five separate logins.
>
> Every document carries its own approval history. A release sign-off happens right inside the file you're reviewing — multi-step, multi-reviewer, quorum-enforced — not buried in a side email thread.
>
> Every one of those actions — logins, views, approvals, deletions — lands in one audit trail. Filter by user or date, watch it flag suspicious activity automatically, and hand an auditor a CSV export in one click instead of a week of archaeology.
>
> Same experience whether the file lives on FileNet, a mainframe, or Google Drive — and watsonx-powered AI can summarize or answer questions about any of them, on the spot.
>
> This wasn't built with autocomplete. IBM Bob 2.0 ran in Agent mode across the entire build — reading our own hackathon documents to re-scope this project's positioning, fanning out parallel subagents to review the whole codebase at once, and independently tracing a search bug through three layers down to a real concurrency race condition — reproducing it, fixing it, and proving the fix with a 200-trial stress test, all gated by a 217-test suite before anything shipped.
>
> C-ECM: the governance layer every fragmented storage estate is missing — no migration, full audit trail, built end-to-end with Bob.

*(~340 words → ~2:20 of pure narration at a natural pace; the remaining ~35 seconds is silent on-screen action/reaction time built into the demo beat below. Time yourself — if you're running long, trim from the Problem beat first, never from the live demo or the Bob beat.)*

---

## Shot-by-shot list

### Beat 1 — Cold open (0:00–0:12)
**Screen:** Black background, bold white text, one line at a time, no narration:
`"Who approved this?"` → `"Which version is current?"` → `"Where's the audit trail?"`

*Why:* opens on a question every reviewer in the audience has personally asked. No voiceover yet — let it land in silence, then start narrating into Beat 2.

### Beat 2 — Problem (0:12–0:42)
**Screen:** A simple animated or static diagram — icons for FileNet, SharePoint, Google Drive, S3, a mainframe — disconnected, with a red "?" between them. Or a fast montage of real app windows (a SharePoint tab, a Drive tab, an email thread, a spreadsheet) to make it visceral.
**Narration:** "When an auditor asks... systems you can't move." (paragraphs 1–2 above)

### Beat 3 — Transition (0:42–0:49)
**Screen:** C-ECM logo/title card: **"C-ECM — Centralized Enterprise Content Management"**, tagline **"Governance over every backend. Zero migration."**
**Narration:** "So we built C-ECM..." (paragraph 3 above)

### Beat 4 — Live demo (0:49–2:19, the 90-second core — do not shortchange this)
Record this as one continuous screen capture; cut narration over it afterward if that's easier than talking live.

1. **(0:49–1:04, 15s) Global Search across backends.** Open C-ECM, click Global Search, type a query. Let results stream in grouped by connection (FileNet / Google Drive / Local) with provider badges visible.
2. **(1:04–1:24, 20s) Approval workflow, acted inline.** Click a result to open the full-screen document viewer. Expand the Approvals section. Click Approve. Show the vote recorded and the step advance.
3. **(1:24–1:49, 25s) Reports / audit dashboard.** Navigate to Reports. Set a date range, filter by the Approvals category, show the chart/donut update. Point out an alert flag (e.g., a failed-login burst). Click Export CSV.
4. **(1:49–2:04, 15s) Second backend, same experience.** Switch the active connection (sidebar) to FileNet or IBM i. Show the same grid/viewer working identically — proof this isn't just a Google Drive wrapper.
5. **(2:04–2:19, 15s) AI Insights.** Open AI Insights on a document, click Summarize, show the watsonx-powered result appear.

*Creativity note: the hackathon guide explicitly says judges will see many similar screen-shares. A visible cursor with subtle click-highlight animation, and a light zoom-in on whatever you just clicked, goes a long way toward not looking like every other entry.*

### Beat 5 — Built with IBM Bob (2:19–2:47)
**Screen:** Cut away from the app to a recording (or screenshots) of an actual Bob Agent-mode session — ideally the real session that found and fixed the Global Search concurrency bug, or the parallel-subagent code-review output.
**Narration:** "This wasn't built with autocomplete..." (paragraph 6 above)

*This beat is not optional — the guide requires the video to "clearly demonstrate how you utilized IBM Bob." Showing an actual session, not just claiming it in narration, is what will separate this from entries that only mention Bob in passing.*

### Beat 6 — Close (2:47–2:55)
**Screen:** End card: C-ECM logo, one-line differentiator, team name, repo link.
**Narration:** "C-ECM: the governance layer... built end-to-end with Bob." (final paragraph above)

---

## Production checklist

- [ ] Record at 1080p or higher; zoom the browser to ~110–125% so on-screen text is legible in a small embedded player
- [ ] Turn on cursor highlighting / click animation if your recorder supports it
- [ ] Keep background music low and instrumental-only, and drop it entirely under the Bob beat so that narration is the clear focus
- [ ] Do one full read-through with a stopwatch before the real recording — trim the Problem beat (not the demo or Bob beat) if you're running over 2:55
- [ ] Upload to YouTube, Vimeo, or Google Drive and set it **publicly accessible** — these are the platforms that get automated AI feedback in your submission confirmation email; other hosts still qualify for judging but skip that feedback
- [ ] Double-check the final render is under 3:00 — judges are instructed to stop watching at the 3-minute mark, so anything after is invisible to scoring
