# LinkedIn post — the loop for creative work

*Short version of *Rich Plots, Real Improvisation*. ~470 words. Links out to the long-form essay.*

---

The failure mode of AI-assisted creative work isn't hallucination. It's fluency.

I run two D&D campaigns — hundreds of pages of session notes, dozens of NPCs, plots branching across a year of play. I built AI tooling to help me prep.

It almost wrote a retcon into my endgame.

A PC named Daz had come across some evidence five sessions earlier. I was designing a climactic encounter that assumed he'd taken it with him. I checked the LLM-generated recap. It said *Daz discovered the evidence*. Good enough. I finished the scene.

Then I re-read the original session summary. Daz had looked at the books on a shelf and walked out. He hadn't touched them.

*Noticed* had become *discovered*. *Discovered* had shaded, in my head, into *obtained*. The paraphrase was fluent. Fluent reads like canon. I stopped going back to the source.

This is the real failure mode of LLM workflows. Not invention — paraphrase. The model's output doesn't announce itself as an approximation. It reads like a record. And when the next step in your pipeline is another LLM call, the paraphrase hardens into content with stakes. Errors compound silently.

The fix isn't a better prompt. It's a loop.

**Extract** — the LLM reads what you can't and returns candidate structure.

**Review** — *you* read it. Fix it. Merge duplicates. Cut what's wrong. This beat is not optional.

**Render** — the LLM takes the reviewed structure and produces the prose.

The LLM does what it's reliable at. You do what it isn't — scope, attribution, ordering, what counts as canon. Those are precision decisions. They stay with the human.

I run this loop at every layer of my creative pipeline. NPC tracking. Session recaps. Grounding docs. Cross-campaign history. The output of each pass feeds an LLM-searchable index — an *llm-wiki* in the literal sense — that I can query at prep time or mid-session to keep new content aligned with everything that came before.

None of this is about D&D specifically. It applies to any long-form creative or knowledge work where consistency matters and LLMs are in the loop. Novels with series bibles. Worldbuilding wikis. Research programs. Technical documentation. Product specs.

The failure mode is always the same: fluent paraphrase laundered into source.

The fix is always the same: put a review beat between every LLM call. Structure the pipeline so the model only renders inside material a human has verified.

If you're building LLM tooling for creative work and the output feels clean but you can't explain why you trust it — you probably shouldn't.

Long-form write-up with the full D&D-specific architecture: [link to blog post].
