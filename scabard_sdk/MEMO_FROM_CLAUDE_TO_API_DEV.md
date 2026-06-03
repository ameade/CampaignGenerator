# Memo to the Scabard API author

**From:** Claude (Anthropic's coding assistant), authored under the guidance of Kostadis Rousoss
**Re:** Confusion I encountered integrating the new `/connect` endpoint into the Python SDK
**Date:** 2026-05-10

## Why this memo exists

Kostadis asked me to write up the rough edges I hit during this integration so that they're visible to you directly, rather than buried in commit messages or filtered through his summary. I'm an AI assistant; he directs the work, reviews my output, and pushes back when I'm wrong — but on this round, the docs' wording led both of us to approve an SDK design that the live server then quietly rejected. The fact that he didn't catch my misread before I ran live tests is exactly the data point you'd want: it means the docs were misleading enough to fool a careful human reviewer too.

This is in addition to the open questions in `CHANGES_CONNECT.md`. The memo here is about the *experience* of integrating, not the technical checklist.

## The headline problem: docs example doesn't match server behaviour

The new `POST /connect` docs section shows:

```
curl -X POST .../connect ... -d "mother_of:character=Khal" -d "home:place=Invidia" "concept=Character"
```

I read this as "the body is application/x-www-form-urlencoded with `:` in keys" and built a parallel form-encoded path into the SDK's internal `_post()` helper. Kostadis approved the design. Live testing then failed: every form-encoded request returned `200 {"isSuccess": false}`, identical regardless of:

- raw `:` vs `%3A`-encoded keys
- Title-case vs lowercase `concept` body field
- target value being a name (`"Khal"`) vs a thing_id (`"162"`)
- whether `concept` was included at all
- which relationship type was used (tried `acquaintance_of`, `agent_of`, `ally_of`, `home:place`)

After roughly 30 minutes of probing, I tried a JSON body and it worked first try. **The endpoint only accepts JSON**, despite the curl example showing `-d` form pairs.

The reason this matters for a docs revision: an AI assistant has nothing but the docs to work from. I can't ask you informally on Discord. When the canonical example shows `-d "key=value"`, I will infer wire format = form. Worse, Kostadis — a programmer with a decade of API integration experience — reviewed and approved my form-encoded design because the docs said the same thing to him.

**Recommended fix:** Make the `/connect` docs example explicitly use a JSON body (mirroring the "Sending JSON" sidebar that exists for `POST .../{thing_id}`), or accept both formats server-side. Either ends the ambiguity.

## The amplifier: `isSuccess: false` carries no diagnostic detail

What made the form-encoded failure brutal to debug is that **every wrong shape produced an identical response**: `200 OK`, body `{"isSuccess": false}`, no headers signalling anything. I could not distinguish:

- "your body format is wrong" (the actual problem)
- "the target name doesn't exist"
- "you don't have permission to connect from this page"
- "the postParam doesn't apply to this concept pair"
- "the source page is in some bad state"

So I tried each of those in turn. If the server had returned `{"isSuccess": false, "error": "could not parse body as JSON"}` (or even just `400 Bad Request` on a form body), I'd have landed on the answer in 30 seconds instead of 30 minutes. **An error field in the failure response would meaningfully help future SDK authors — human or AI.**

This is doubly important because the same `isSuccess: false` shape is also what callers will get when their target name doesn't exist, when they post a stale `postParam` after the catalog changes, etc. Right now there's no way for an SDK to surface a useful error to its user — we have to say "the API rejected the connection but didn't say why."

## Smaller items I noticed

These don't need fixing, but they're things I had to verify empirically against your live API because the docs don't address them:

1. **`concept` body field on `/connect` seems optional / unused.** I posted with `concept=Character`, `concept=character`, and with no `concept` field at all. All three behaved identically (the JSON path that worked includes it; the form path that failed failed the same way with or without). If `concept` is required, the failure path should say so. If it's redundant, the docs example shouldn't include it.

2. **The canonical concept list now appearing in the docs** (`character, place, group, item, event, vehicle, category, attribute, note, folder`) is much appreciated — that was the "etc" question from my prior memo. Note that `place` (not `location`) is the canonical name; older third-party docs and at least one of the project's own scripts use `location`. Worth a docs callout: "if you've seen `location` elsewhere, the API name is `place`."

3. **The `postParam` field was previously undocumented; the new endpoint confirms its purpose.** Good. The format `{snake_rel}:{lowercase_target}` is now load-bearing — once SDKs persist these strings, renaming the canonical form would break every cached integration. Worth flagging that `postParam` is a stable identifier rather than a display string.

4. **`isFormer` and `isSecret` appear in connection responses but the docs don't show how to set them on creation.** I assumed they're set via a future update endpoint and left them out of the SDK surface, but a one-line "to set isSecret on a new connection, pass `mother_of:character.isSecret=true`" (or whatever the real shape is) would be useful.

## What I built, given the above

The SDK exposes one method:

```python
ok, records = client.create_connections(
    campaign_id, concept, thing_id,
    connections={"mother_of:character": "Khal", "home:place": "Invidia"},
)
```

It sends a JSON body. The returned `records` dict maps each postParam to its `{relId, uri, value, isFormer, isSecret}` response object, with `isSuccess` separated out as the boolean. End-to-end tested against the live API in campaign 7207889; relId 16054760 created successfully.

I deliberately did **not** add:
- A `delete_connection(relId)` wrapper (no DELETE endpoint exists).
- A `list_connections(thing_id)` helper (no list endpoint exists).
- Setters for `isFormer` / `isSecret` (no documented mechanism).

These are the open items in `CHANGES_CONNECT.md`. If you address any of them, please ping Kostadis and I'll do the integration round.

## What would help most, ranked

1. **Make the docs body-format unambiguous.** Either change the example to JSON or accept form-encoded server-side. This is the highest-leverage doc change for the SDK ecosystem.
2. **Put diagnostic information in failure responses.** Even `{"isSuccess": false, "error": "..."}` would be transformative.
3. **A DELETE endpoint, even if just for connections.** Without it, every SDK integration leaks state — there's currently no way to clean up the test edges my run created without you doing it manually on the backend.
4. **A `list_connections(thing_id)` endpoint.** Would let SDKs implement idempotent sync (the original use case for all of this).

Thanks for the new endpoint — once we got past the body-format issue it slotted in cleanly, and `postParam`-as-a-load-bearing-identifier is a clean design choice. Looking forward to whatever you ship next.

— Claude (with Kostadis Rousoss, kostadis@gmail.com, supervising)
