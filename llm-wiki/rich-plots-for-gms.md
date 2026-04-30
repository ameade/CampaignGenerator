# Rich Plots, Real Improvisation

So I was in a Discord channel the other day, trying to share a tool I'd built, and a new DM named Alice wrote back: *I'm gonna be so honest… this is Greek to me, I have no clue what you're showing, what problem it solves… I'm a new DM.*

Fair.

I dropped a link into the chat — a Google doc I use to prep my *Out of the Abyss* campaign — and a minute later she wrote: *Ooooooooh it's for your campaign story. I thought it was a software thing, not creative.*

This essay is the thing I should have shared instead of the architecture diagrams.

---

## What Alice saw

Four rows from the top of the doc. The party is called the Ember Vanguard.

> **Zuggtmoy's Wedding** — *Zuggtmoy / Neverlight Grove*
> Current: Elevated — 2 increases (Blingdenstone expansion + Basidia's evacuation removed internal resistance).
> Next threshold: Wedding completion / Araumycos union.
> What triggers next: Continued party absence from Underdark; no faction opposes Zuggtmoy; fungal spread reaches critical mass.
>
> **Juiblex Rebirth** — *Juiblex*
> Current: Low-Moderate — 1 increase (declared intent to consume Zuggtmoy's domain).
> Next threshold: Juiblex manifests a new physical form or begins attacking Zuggtmoy's territory.
> What triggers next: Zuggtmoy's wedding weakens her defenses; time passes without intervention.
>
> **Gracklstugh Destabilization** — *Shal / Deepking / multiple factions*
> Current: High — 4 increases (Eldgrim memorized contracts; Shal unchecked; Hgraam sealed cavern; irreversible two-headed giants).
> Next threshold: Open civil conflict or Deepking's purge begins in earnest.
> What triggers next: Any assassination from Eldgrim's list carried out; Shal's influence deepens; no external stabilizing force intervenes.
>
> **Drow Pursuit** — *Ilvara (dead) → House T'sarran*
> Current: Moderate — 1 increase (Jimjar vanished, no guide, extended Underdark exposure).
> Next threshold: Drow hunting party reacquires the trail or intercepts near surface exits.
> What triggers next: Party delays in tunnels; T'sarran survivor reaches Menzoberranzan; Nym/Kaelira report party's route.

Four schemes. Four clocks. Every "increase" was earned by something the Ember Vanguard did or didn't do at the table. I scan this in a minute before every session and I know — exactly — what has moved since last time and why.

Under it, the same doc continues. A six-paragraph dossier for Zuggtmoy — active plans, what the party knows vs. what they don't, key relationships, a line that reads *Thorin (attempted to seduce him; he declined but heard her melody twice — vulnerability persists)*. A state block for every faction. Eight numbered plots with urgency levels.

I can't run the campaign I want to run without this doc.

I also can't write it by hand. Not across two campaigns. Not across a year of sessions. Not with forty named NPCs each dragging their own history around.

This essay is about how I got it written anyway.

---

## Why I even want villains like this

I played Baldur's Gate 3 cold. No guide. No walkthrough.

Somewhere in it I decided — in character, as Shadowheart — that if I helped Gale ascend to godhood I could leave her entire bloody life behind. So I saved the tieflings. I didn't recruit Lae'zel. I became a Dark Justiciar, expecting some kind of superpower, and got nothing. Then I killed my parents, not for the dark urge, but because I was *furious* at the woman who had dragged Shadowheart through all of it and I wanted her dead and every trace of her gone.

Apparently about fifty people have ever finished the game that way.

That's the kind of player I want at my table.

I run two D&D campaigns. *Out of the Abyss*, heavily modified — the Ember Vanguard — and a *Dragon of Icespire Peak* / *Lost Mine* hybrid set in Phandalin. I actively push my parties to go somewhere I didn't plan for. If the plot I prepped isn't the plot they want, the plot I prepped doesn't matter.

That's the deal.

The deal has costs.

---

## What I wanted that was impossible

Three things at once.

One. **Deep prep.** Texture. NPCs with interiors. Villains whose behavior today is a consequence of something they chose eight sessions ago. Plots that advance whether the party is watching or not — Zuggtmoy is preparing a wedding whether or not the Ember Vanguard shows up to stop her.

Two. **Flexibility.** When the party walks past the dungeon I built, something has to be where they actually went. And it has to feel like it was always going to be there.

Three. **Consistency.** The villain I run in session 24 has to behave like the villain I ran in session 6. If Shal contradicts himself because I forgot, the illusion cracks, and everyone at the table feels it.

Pick two.

Prep deep, and the moment they deviate, you're improvising on top of prep that doesn't apply. Prep loose, and the world feels thin. Try to prep both branches, and you burn the time you were trying to save. Meanwhile, across a year of sessions, you quietly lose track of what the villain has actually done. The next scene you write for him drifts. Nobody calls you on it. The fiction just feels a little less alive.

And here's the part people don't say out loud. When the party walks past four hours of prep, I don't mind *that they didn't use it*. I mind *that I wasted four hours.* Half-time the next session. Compounding. Rich plot, tired GM, thinner session. And the players can feel it, too — they don't want to go down a path I didn't prep any more than I want them to.

Before LLMs, I'd given up on all three. I was picking which two I could afford that week.

---

## What I tried, in order

I wrote summaries from memory. Works for one campaign. Fails for four.

I tried getting an LLM to write the summaries. Better than nothing. But the output was imprecise in ways I didn't always catch, and the errors would surface two months later in a scene I was about to run.

I gave up. For a while.

Then I found GMAssistant. Real improvement. Accurate summaries of what *happened*. But a D&D session isn't a sequence of actions. A D&D session is a sequence of *dialogue and interaction*. The action was correct. The feel of the session — Thorin's conversation with Glabbagool, Zalthir's warnings to Grygum, the exact words Phylo said when the mask slipped — wasn't in it.

So I went down a rabbit hole. Augment the GMAssistant recaps with verbatim VTT transcripts from the Zoom call. Build tooling around that. Build more tooling around the tooling. Six weeks of real work, maybe two months. About $1000 in tokens.

I thought I was solving the record problem.

I was. Partly.

What I didn't realize — not until it almost cost me a scene I was about to run — was that I was also building a new kind of failure.

---

## The scene I almost ran

A few months into *Out of the Abyss*, I was designing the endgame.

The design leaned on an earlier scene. A PC named Daz had come across evidence implicating a major NPC. My encounter assumed he'd taken the evidence with him.

I checked the LLM-generated recap to confirm. It said: *Daz discovered the evidence.* Good. I finished the scene.

Then, by accident, I re-read the original session summary.

What had *actually* happened: Daz had looked at the books on a shelf, noted they were unusual, and walked out of the room. He hadn't opened them. He hadn't taken them.

*Noticed* had become *discovered* in the paraphrase. *Discovered* had shaded, in my head, into *obtained*.

The encounter I'd built would have been a retcon against my own campaign. Players notice retcons. Some fraction of the fiction's weight leaks out and doesn't come back.

And here's the thing. The LLM hadn't lied. It had *paraphrased*. And the paraphrase was fluent. It read like canon. It read like canon so smoothly that I stopped going back to check.

That's the failure mode. The LLM's output doesn't announce that it's a paraphrase. It reads like a record. And if the next step in my pipeline is another LLM — synthesizing the threat tracker, say, from the summaries — the paraphrase hardens into dossier entries and arc scores, pointed at my players.

Errors don't stay small. They compound.

My first version of the tool had traded consistency for depth and flexibility. And the trade was invisible.

The obvious fix — re-read the source every time — erases the value. If I re-read every source every time, I don't need the LLM.

---

## What actually worked

What worked was putting myself back in the middle.

Three beats. I run them at every layer now.

**Beat one.** The LLM reads something I can't read fast. A transcript. A stack of summaries. A year of sessions. It hands back candidate structure. A list of NPCs. A draft dossier. A set of scenes to narrate.

**Beat two.** *I* read what it produced. I fix the names. I merge the duplicates — *Captain Tolubb* and *Cap. Tolubb* and *Tolubb* are one NPC, not three. I cut paraphrased lines that crossed into invention. I add what's missing.

This beat is not optional. This beat is the job.

**Beat three.** The LLM takes the reviewed structure and writes prose. A dossier. A narrative recap. The threat tracker at the top of this essay.

It renders inside a frame I've verified.

The LLM is strong at beats one and three. It's unreliable at beat two — scope, attribution, ordering, what counts as canon. Those are creative decisions. Those stay with me.

Skip beat two, and the errors compound. Keep beat two, and the loop holds.

The threat tracker you saw at the top is the direct output of this loop. Session summaries get extracted into per-NPC dossiers. I merge the duplicates. I fix the scopes. Then the tool synthesizes the planning document using my reviewed dossiers as the source of truth. What comes out is the four-clocks document. Consistent, because I checked the scopes. Fast enough to update between sessions, because the LLM did the reading.

Same loop for session recaps. Same loop for the per-session NPC cheat sheet I hand to myself before every game. Same loop for the cross-campaign canon that lets Ember Vanguard's story and the Phandalin group's story live inside one world.

The loop is the whole trick.

---

## What I have now

A threat tracker that tells me, before every session, which of my villains' plans have moved since last time and what moved them.

Session recaps that read like novel chapters. First-person per character, in their own voice, quoting dialogue that was actually said at the table — because I verified it.

NPC dossiers where *Captain Tolubb* is one NPC. And where Zuggtmoy's six-paragraph entry knows about both Araumycos and Thorin.

Two campaigns. One world. Coherent across a year of play.

A GM who doesn't mind anymore when the party walks past the dungeon. Because the prep isn't the kind of work that gets wasted.

---

## If you want this too

The tools are free. They're also crude — I'm one GM iterating on my own campaigns, not a product team. About $1000 in tokens to get this far. The learning curve isn't zero.

I don't make money on any of this. It's a hobby. My goal is that more of us have better tools, and that the people trying to make a living at this can actually make a living at it.

So if something here sparks something for you — build the version you want. If something I built is useful, take it. Send it to a friend who runs games. Fork it. Break it.

I'd rather more of us had working tools than fewer.

There's an architectural version of this essay coming — longer on the loop, the trust layers between documents, the searchable index I use to query reviewed content mid-session. Shaped for builders.

This one was for Alice.

---

*The code and the architectural write-up: [links]. A follow-up once I've run the mid-session retrieval at a live table.*
