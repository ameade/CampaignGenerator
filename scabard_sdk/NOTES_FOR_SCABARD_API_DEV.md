# Notes for the Scabard API developer

> **Update (2026-04-28):** stolph pushed back on items #2 and #3 below, and he
> was right. After running `list_connection_types` against a live campaign
> across `character` / `place` / `event` / `group` (226 entries total):
>
> - **#1 (`isSymmetric` sometimes missing):** every live entry includes the
>   field. The docs example shows it omitted on some entries, but live
>   responses don't reproduce that. Withdrawn — not actionable, though the
>   docs example could be made consistent.
> - **#2 (mixed casing):** withdrawn. The rule is consistent — URLs are
>   lowercase, body/response data values are Title Case. The SDK's `.title()`
>   on POST `concept` matches the rule rather than fighting it.
> - **#3 (`source` doesn't match URL concept):** withdrawn. Live responses
>   have `source` matching the URL concept on 100% of entries
>   (114/114 Character, 38/38 Place, 26/26 Event, 48/48 Group). The original
>   note was extrapolating from the docs response example, which appears to
>   mix entries from different concepts to illustrate the schema. No bug.
>
> The "things that would help SDK authors" section below stands as-is.

Hi — I integrated the new `GET /api/v0/campaign/conntypes/{concept}` endpoint
into the Python SDK today. Thanks for adding it. A few small pieces of
feedback while it's fresh, in case any of it is useful for the next pass on
the API or the docs.

## What worked well

- The campaign-agnostic URL (no `campaign_id`) matches the reality that
  connection types are a global catalog — that's the right shape.
- Returning a flat list under `connTypes` made the SDK wrapper a one-liner on
  top of our existing `_get()` helper.
- Lowercase `concept` in the URL is consistent with the other GET endpoints,
  so callers don't need to remember a special casing rule for this route.

## Small inconsistencies / things that surprised me

1. **`isSymmetric` is present on some entries and absent on others.**
   The example in the docs shows:
   ```json
   {"isSymmetric": false, "rel": "Setting of", "source": "Place", "target": "Event"},
   {                       "rel": "Setting of", "source": "Place", "target": "Adventure"}
   ```
   I assumed "missing means false" and coded it that way, but it would be
   nicer if the field were always present (even if always `false` for
   asymmetric relationships). Right now every consumer has to remember to
   `.get("isSymmetric", False)` instead of `["isSymmetric"]`, and a future
   reader of the JSON might reasonably wonder whether the absence means
   "false" or "unknown / not applicable".

2. **`source` / `target` are title-cased (`"Place"`, `"Event"`), but every
   other endpoint takes lowercase concepts in the URL.**
   So a caller who lists conn types and then wants to call
   `GET /campaign/{id}/{concept}` has to lowercase the value first. Same
   thing the create-page POST already does in reverse — the URL is
   lowercase but the body field `concept` must be title-cased. The mixed
   casing across endpoints is workable but a small papercut; if one of the
   two conventions is the "real" one, picking it everywhere would make
   client code cleaner.

3. **`source` doesn't always match the concept in the URL.**
   For `GET /campaign/conntypes/character`, I'd expected every entry to have
   `source: "Character"`, but the example response has `source: "Place"` for
   entries returned from `/conntypes/character`. That suggests the endpoint
   actually returns "all connection types involving this concept" rather
   than "connection types where this concept is the source." That's
   probably the more useful behaviour, but the docs phrase it as
   "Connection Types of a certain concept," which I read the wrong way at
   first. A one-line clarification ("returns every type where the concept
   appears as either source or target") would prevent the same confusion
   downstream.

## Things that would help SDK authors

- **A `404` for unknown concepts would be ideal.** I haven't tested what the
  endpoint does for `/conntypes/not-a-real-concept` — if it 200s with an
  empty list, callers can't distinguish "this concept has no connections
  defined" from "this concept doesn't exist at all." A `404` (matching the
  status code table in the docs) would let SDKs surface a typed
  `NotFoundError`.

- **Listing the valid `concept` values somewhere stable.** The docs say
  "lower case and can be in [character, group, event, etc]" — the `etc` is
  the painful part. A small enum endpoint, or even just a fixed list in the
  docs, would let SDKs validate inputs at the client side instead of
  round-tripping every typo.

- **Returning the new page's ID from `POST /campaign/{id}/{concept}`.**
  Unrelated to conntypes, but while I'm here: the create endpoint returns
  `{isSuccess: true}` only, so the SDK has to re-fetch the page list and
  match by name to discover the new `thing_id`. Having the response include
  `thing_id` (or the full URI) would remove a 1-second sleep + extra GET
  from every create.

- **A `DELETE` endpoint, or even a documented "soft delete" via update.**
  The integration test currently leaves a "[TEST PAGE - SAFE TO DELETE]"
  page behind on every run because there's no way to remove it
  programmatically. Anyone running a sync from a CI pipeline ends up with
  the same problem at scale.

None of this is blocking — the new endpoint slotted in cleanly and the SDK
shipped today. Just flagging the rough edges in case they're useful when you
revisit this area.

— Kostadis
