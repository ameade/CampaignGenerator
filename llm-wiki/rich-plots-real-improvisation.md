# Rich Plots, Real Improvisation

*Notes from running D&D with LLMs.*

---

## The trilemma

I run two D&D campaigns. *Out of the Abyss*, heavily modified. And a *Dragon of Icespire Peak* / *Lost Mine* hybrid set in Phandalin.

Between them: hundreds of pages of session summaries, dozens of NPCs, two plots branching for over a year.

Every GM I know wants three things at once.

A campaign that's **deeply prepped** — with texture, with NPCs who have agendas, with plots that advance under their own momentum whether the party notices or not.

A campaign that's **flexible** — that bends when players go somewhere you didn't plan for. Which is always. Players are the point.

A campaign that's **consistent** — where session 24 honors session 6, where NPCs don't change their minds between games unless something actually happened, where nobody at the table says "wait, didn't we already kill that guy?"

Prepped. Flexible. Consistent.

Pick two.

That's the GM's trilemma. Prep deep and you're brittle — the party walks past your dungeon and you're either railroading or improvising on top of prep that no longer applies. Prep loose and the world feels thin. Compromise and you get both.

This essay is about the tooling I built to stop picking two.

Not a product pitch. Working notes from a GM on what broke, what worked, and the pattern that ended up holding three things together.

---

## Why this used to be impossible

Before LLMs, the leg that went first was consistency.

Prep depth is achievable. It just costs hours per session. Flexibility within deep prep is achievable too — deep prep gives you material to pivot to.

What isn't achievable, not by anyone I've ever met, is holding the full state of a long-running campaign in your head.

A campaign with eighteen sessions and forty named NPCs is a body of information larger than memory. You remember what stuck. You forget the cleric from session 11. You half-remember the stone giant's oath and improvise around the gap. Two sessions later, you've accidentally rewritten what he said.

It's not a failure of effort. It's bandwidth.

The standard responses are well-worn. Run shallower campaigns so the state fits in your head. Run published modules and let the book do the remembering. Keep a wiki you don't have time to update. Keep a journal you don't have time to re-read.

All of these work, to a point. None of them break the trilemma. They just lower the ceiling so the trilemma bites less.

I wanted the ceiling back.

---

## Where LLMs break

LLMs are, on their face, the missing piece. They can read everything I can't.

My first version of the pipeline was exactly that. Summaries in, prep out. The LLM reads everything. I read its output. Done.

It wasn't done.

A few months into *Out of the Abyss*, I was designing an endgame encounter that leaned on an earlier scene. A PC named Daz had come across evidence implicating a major NPC. The encounter I was drafting assumed he'd taken the evidence with him.

I checked the LLM-generated recap. It said: *Daz discovered the evidence.* Good. I finished the encounter.

Then, by accident, I re-read the original session summary. What actually happened: Daz saw the books on a shelf, noted they looked unusual, and left them there. He hadn't taken them. He hadn't opened them.

*Noticed* had become *discovered*. *Discovered* had, in my head, shaded into *obtained*.

The encounter I'd built was internally consistent. If I'd run it, it would have been a retcon. Players notice retcons. Some small fraction of the fiction's weight leaks out.

The interesting part is the mechanism.

The LLM hadn't lied. It had paraphrased. And the output was *fluent*. It read like canon. It read like canon so smoothly that I stopped going back to check.

That's the failure mode. The LLM's output doesn't announce that it's a paraphrase. It reads like a record.

If I ask the LLM to *also* do the next step — generate the encounter from the paraphrased recap — it will happily do so. The paraphrase hardens into a scene with dialogue and stakes, pointed at my players. Errors don't stay small. They compound.

So the naive version traded away consistency to gain depth and flexibility. And the trade was invisible.

The obvious fix — verify every output by hand — erases the value. If I re-read the source every time, I don't need the LLM.

The real question was different. Could I structure the pipeline so the LLM does only what it's reliable at — rendering verified structure — and a human review step sits at every point where precision matters?

That's what the rest of this is about.

---

## The extraction layer I don't own

Before any of my tooling runs, something has to turn the raw session — hours of unstructured speech on a Zoom VTT — into usable signal.

I don't do that part. Other people have built good tools for it. GMAssistant.app. Saga20. A handful of others in the AI-assisted D&D space.

My own pipeline leans on GMAssistant directly. The session doc generator uses its recap as an authoritative anchor for scene extraction. Without it, extraction is an unguided scan across the transcript, and unguided scans miss things.

The ecosystem matters. Crediting it matters. The tools I built sit on top of a layer other people built.

That said: the output of the VTT extraction layer is still an LLM extraction. Fluent. Plausible. Sometimes wrong in ways you won't catch without reading the source.

The review beat applies to their output too. Everything downstream of the VTT passes through my gate before anything else runs.

---

## The loop

The pattern I ended up with wasn't elegant the first time. I didn't arrive at it by principle. I arrived at it by iterating on two live campaigns and paying attention to where things kept breaking.

The shape my iteration settled into is three beats.

**Extract.** An LLM reads something I can't read fast — a transcript, a session summary, a stack of extractions — and returns candidate structure.

**Review.** I read the candidate structure. I fix it. I merge it. I throw out what's wrong. I add what's missing. This is not optional. This is the thing.

**Render.** An LLM takes the reviewed structure and produces readable prose — a narrative recap, an NPC dossier, an encounter document. It's rendering inside a structure I've verified.

That's it.

The LLM is strong at beats one and three. It's unreliable at beat two — scope, ordering, attribution, what counts as canon. Those are precision decisions. Those are mine.

Skip the review beat and errors compound. The first LLM's paraphrase becomes the second LLM's input. Two LLMs downstream, the original detail is unrecognizable. Nobody notices because the output is fluent.

Keep the review beat and the loop holds. The LLM does what it's good at. I do what I'm good at. The content that comes out has actually been seen by a human.

I now run this loop at every layer of my pipeline. There are four seams where it lives. They're worth walking through.

---

## Seam one: NPC dossiers

The party meets an NPC in session 4. They reference him again in session 9 with a slight typo on the name. In session 15, someone else mentions a character who may or may not be the same person.

Without help, I now have three fragments of one NPC and no single view.

The script I use — `planning.py --build-dossiers` — extracts per-NPC information from every summary. Each dossier lives in its own file. *Extract.*

Then I read them. And half the time I find duplicates under different names. Captain Tolubb. Cap. Tolubb. Tolubb. Three files, one NPC.

I merge them. Pick the canonical file. Fold the names into an `aliases:` block at the top. Reconcile any content differences by hand. Delete the losers. *Review.*

When I run `planning.py --synthesize` on the reviewed dossiers, the synthesizer uses the aliases to rewrite every occurrence of "Cap. Tolubb" in the raw extracts to "Tolubb" *before* the LLM sees the text. The final planning doc treats him as one NPC. *Render.*

Skip the review beat — the merge, the alias recording — and synthesis treats every variant as a distinct NPC. You get the fragmented result back, now laundered into a clean-looking document. Exactly what the merge was supposed to fix.

The script can extract. I have to review. The script can render.

Three beats.

---

## Seam two: session recaps

After a session, I have a VTT transcript, a GMassist-style recap, and a few hours of memory.

The session doc generator runs passes one through four — consistency check against campaign state, enhancement of structured sections, narrative plan, per-scene character extraction. *Extract.*

Then I stop the pipeline. Each scene's extraction is written to a file. I open them in an editor. I read them against the VTT source. I add dialogue the extractor missed. I cut lines the extractor invented. I adjust emphasis when it got the emotional beat wrong. *Review.*

When I'm satisfied, I run pass five — narration — from the reviewed extractions. One character, one scene at a time. *Render.*

What comes out is a session document that reads like a novel chapter. First-person per character. Style-matched to their voice. Dialogue that was actually said, because I verified it in beat two.

If I skipped the review beat, the narration would be fluent and wrong in ways I couldn't see without checking the VTT. Same failure mode as the Daz scene, one layer deeper.

---

## Seam three: grounding docs

The bible of my campaign is the accumulated session summaries. Too long to read every time. Too important to not use.

`distill.py` and `campaign_state.py` extract from the bible. World state. NPC states. Completed quests. Open threads. *Extract.*

I read the generated `world_state.md` and `campaign_state.md`. I fix the things that are wrong. I add the things the extractor missed. I cut the things that are no longer true. *Review.*

Those reviewed docs are now the grounding context for every downstream prep script. `prep.py` reads them first. Everything I generate is rendered against a world state I've verified. *Render.*

This is the least visible seam. It's also the one that makes all the others work. Prep generated from bad grounding is bad prep, no matter how good the prep prompt is.

---

## Seam four: cross-campaign canon

I don't just run two campaigns in parallel. I run two campaigns that share a world.

Group 2's actions become Group 1's history. Group 1's consequences become Group 2's present. This is fun and also a lot to keep straight.

I keep a `notes/canon/` directory. Cross-campaign events go there when they happen. The party in one campaign did X; here's what the other campaign's world now has to account for. *Extract.* (Often by hand, not by LLM.)

I review the notes before they touch either campaign's grounding docs. Often I'm the only person with enough context to know what an event actually means across the shared world. *Review.*

When I'm sure, I promote the relevant facts into the appropriate campaign's `world_state.md` and NPC dossiers. *Render.*

`notes/` is staging. Neither campaign's palace indexes it directly. The canon gate runs across both worlds.

---

## The query that kept failing

The loop gives me clean content. Reviewed dossiers. Verified grounding docs. Session recaps I trust.

Clean content in files is also passive content.

Four months into running OotA, mid-session, the party doubled back through a village they'd passed through five sessions earlier. Someone mentioned *that bartender we met*. I had six seconds before my table noticed I didn't know.

I tried to ask NotebookLM.

"Hey NotebookLM, what's the name of this person in this village the party met?"

The answer came back confident, fluent, and wrong. Wrong name. Wrong village. Wrong session.

NotebookLM doesn't know what "the party" means. It has no persistent roster of my PCs. It has no trust layering — an NPC mentioned in a planning draft weighs the same as an NPC in a session summary, and the planning draft is *speculative*. It summarizes when I need it to return hits. It loses temporal context — *met in session 4* looks the same as *mentioned in passing in session 11*.

Every time I tried to use it mid-session, the same thing happened. Fluent. Confident. Wrong.

This is the naive LLM problem from the Daz scene, one layer up. NotebookLM is doing extract + render with no review beat in between, no trust awareness, and no sense of which documents count as what.

So I had clean content I couldn't query reliably. I'd solved the consistency leg with the loop and then rediscovered the same failure mode at the retrieval layer.

The flexibility leg wasn't going to come from a better prompt.

---

## Memory palace

The metaphor for what I built is older than computing. A memory palace. A building in your head where you place things so you can walk through and find them again.

Mempalace is that, externalized. A searchable index over the reviewed content the loop produces.

The important word is *reviewed*. The palace doesn't index my raw summaries. It doesn't index speculative notes. It doesn't index the unrevised output of LLM extraction. It indexes what the loop has blessed.

The palace has structure. Three layers, different trust levels.

**Narrative.** The campaign bible, split into chapters. Authoritative. What actually happened at the table. If the palace tells me the narrative wing says X, X happened.

**Chronicle.** LLM extractions of the bible, organized by time. Search accelerator. Fast way to find the right session or the right window. Not authoritative on its own — the paraphrase problem still applies — but good at answering *when did this matter*.

**Reference.** Reviewed grounding docs, NPC dossiers, world state. Working reference. What's currently true. Stable between sessions.

A query crosses the wings. Find the NPC in reference (who is this). Check chronicle for the time window (when did this matter). Verify in narrative (what actually happened).

That last step is the one NotebookLM doesn't have. NotebookLM returns a paraphrase and calls it an answer. The palace returns a paraphrase *and* tells you the source paragraph. You can verify, in seconds, before committing.

Trust layering plus source retrieval plus structure-aware search. That's the mechanism.

---

## Prep time: the Daz problem, solved

Back to the Daz scene.

The question I should have run, before building the endgame encounter: *what did Daz do with the evidence in that session?*

In the palace: search the chronicle wing first — find the session where the evidence came up. Then pull the actual scene from the narrative wing. Read the two paragraphs. Three minutes, maybe.

What I would have found: Daz looked at the books, said something in character about them being unusual, and walked out of the room without touching them.

The encounter I was building would have been redesigned on the spot. Probably better for it — *he knew and did nothing* is a more interesting story beat than *he took the evidence*, for the kind of character Daz is.

That's the consistency leg, live.

I now do this before every major prep beat. Anything I'm about to build downstream canon on gets a palace check first. Five minutes. Catches drift before it ships.

The seams of the loop feed the palace. The palace feeds the next round of prep. Clean content in, clean questions answered, clean content back out.

---

## Mid-session: what I'm building toward

This is where the essay has to be honest.

I've been running the loop long enough to trust it. Prep-time palace use is in my workflow now. Consistency leg, back.

Mid-session use is the next chapter. I haven't run mempalace at a live table yet — it's new. What I've done before now is variants of the NotebookLM attempt, and I've reported how those went.

Here's what mid-session use is designed to do.

*"Hey palace, what's the name of the bartender in Phandalin the party met in session 4."*

The palace knows *the party* — my PCs are listed in reference. It knows *bartender* maps to NPCs in the Stonehill Inn. It knows *session 4* is a narrative-wing filter. It returns the two paragraphs where the encounter happened.

I read the name off the screen. I use it at the table. The scene keeps moving.

That's the flexibility leg. Not *the LLM generates an answer*. *The palace retrieves the passage and I read it*. The retrieval is fast because the index is built on reviewed content. The answer is trustworthy because I'm looking at the source, not a paraphrase.

The design is done. The architecture matches the failure modes I diagnosed from NotebookLM. The at-the-table running-time is the thing I haven't proved yet.

I'll write the follow-up when I have.

---

## Cross-campaign: the ceiling

The most interesting queries cross campaigns.

Group 2, months ago, captured Lolth as part of a deal with Vhaeraun. That action motivated the Faerzress Orthodoxy to convince Gromph Baenre to perform the ritual that triggered the demon lord incursion. Which is the premise of *Out of the Abyss*, Group 1's campaign.

One campaign's climactic decision is another campaign's opening premise. When Group 1 asks *why are the demon lords here*, the correct answer involves Group 2's choices from a year ago.

In notes-on-my-laptop world, this falls out of date the first time I forget to update one campaign after something happened in the other. It has fallen out of date before.

With a palace per campaign and shared canon in `notes/canon/` that gets promoted into both, the coherence is maintainable. Query the Group 2 palace for *Lolth*. Query the Group 1 palace for *Gromph Baenre*. Both hits trace back to the same event. The world stays one world.

This is the ceiling the trilemma moves when it breaks. Not just consistent campaigns. *Interlocking* campaigns. Depth that compounds across groups and years.

No commercial tool does this. It's not the market. It's also not a hard problem — once the loop is solid and the palace is structured, cross-campaign canon is mostly an organizational question.

---

## Trust is emergent

One thing I didn't plan: the trust hierarchy fell out of the loop on its own.

A document's trust level is how many review beats it has survived.

Raw VTT — zero review beats. Useful, authoritative in a literal sense (it's the recording), but hard to consume.

LLM extractions of the VTT — one LLM beat, zero review beats. Accelerator, not truth.

Reviewed session summaries — one LLM beat, one review beat. Authoritative for what happened at the table.

Grounding docs synthesized from reviewed summaries — one more LLM beat, one more review beat. Working reference, trustworthy for planning.

I didn't design this. I built the loop to fix a problem. The hierarchy emerged because different documents have been through different numbers of review beats, and I can tell the difference when I'm reading them.

The palace respects the hierarchy because it's built on the output of the loop. The wings correspond to trust levels. Search accelerates by wing. Verification crosses wings.

If I'd tried to design the trust hierarchy up front, I would have gotten it wrong. I got it right by letting it fall out of the work.

---

## The canon gate

One rule I don't bend.

Nothing enters the palace without passing a review beat.

`notes/` is staging. Arc drafts, encounter sketches, NPC ideas, speculative plot threads — all of it stays in notes. None of it gets indexed.

When a note becomes canon — when it's earned its way into the campaign — it gets promoted to the appropriate grounding doc or dossier. Then the palace mines the new file. Then it's searchable.

Not before.

The reason is the same reason the review beat exists at all. The palace's value is that its answers are trustworthy. Start indexing speculative material and the next mid-session query returns a fluent, confident answer based on something I was *thinking about* doing, not something that happened. Exactly the NotebookLM failure mode, now in my own tool.

The gate is the thing. Everything else is support.

---

## Things I tried that didn't work

Worth listing. None of these are sermons — they're my mistakes.

**Indexing everything.** Early on I mined every document I had into the palace. Every session summary. Every extraction dir. The published module. The result was search results dominated by the module and diluted by extraction redundancy. I now index only content with unique retrieval value. Module text lives outside the palace, accessed via the 5etools MCP instead.

**Auto-promoting notes.** I briefly had a script that watched `notes/` and mined anything new into the palace. This took about a week to start returning speculative material as authoritative. Ripped it out.

**Treating session_doc narration as record.** The first-person per-character narrations are great to read. They are not the record of what happened. They're a render of a review of an extraction. If I query the palace and get a hit from a narration file, I still go check the original summary. Same failure mode as Daz — fluent doesn't mean source.

**One-shot LLM prep.** Feeding the LLM the whole summaries file and asking for an encounter. Works for shallow prep. Shreds consistency at depth. The loop exists because this doesn't scale.

None of these were dumb ideas in the moment. They were natural things to try. They all break the same way — by letting the LLM do the architect's job somewhere in the chain.

---

## What I have. What's next.

What I have, today, across two campaigns:

Clean content that a human has actually looked at. NPC dossiers with merged aliases. Grounding docs I trust. Session recaps where the dialogue is dialogue that got said.

A palace built on that content. Prep-time queries that catch the Daz problem before it ships. Cross-campaign coherence that holds across a year of play.

A consistency leg that's back. A depth leg that's real.

What I don't have yet, honestly: live mid-session evidence that mempalace solves the flexibility leg. The NotebookLM attempts told me what doesn't work and why. The palace is designed against those failure modes. I haven't yet queried it under table pressure with a party watching.

I'll write the follow-up when I have.

What I think this generalizes to: any long-form creative project with continuity requirements and LLM help. Novelists with series bibles. Worldbuilders with decades of notes. Researchers with years of reading. The loop doesn't care that it's D&D. GMs happen to be a good test case because campaigns combine volume, continuity, and table pressure.

I'm going to write more about the architectural side of this. Control-plane thinking applied to creative tooling. What the trust hierarchy looks like as a system. Why the extract/review/render pattern is a general answer to LLM-assisted knowledge work, not a D&D trick.

Those essays will be longer on the architecture and shorter on the dice.

This one was for GMs.

---

## The ecosystem

Tools I lean on or built. One line each.

**GMAssistant.app** — turns Zoom VTT transcripts into structured session recaps. First extract beat in my pipeline.

**Saga20** — similar space, different approach to VTT signal extraction. Worth knowing about.

**CampaignGenerator** — my tooling. Session prep, session doc generation, grounding doc synthesis, NPC dossier building. The scripts that run the loop. ([github link])

**mempalace** — the palace itself. Wing/room architecture, trust-layered retrieval, MCP-accessible. Build guide: `MEMPALACE_HOWTO.md`. ([github link])

**5etools MCP** — published module text stays here, out of the palace, accessed on demand.

**notes/canon/** — not a tool, a convention. Shared cross-campaign history. Portable across workspaces.

---

*Follow-up essay, after I've run this at a live table, will report.*
