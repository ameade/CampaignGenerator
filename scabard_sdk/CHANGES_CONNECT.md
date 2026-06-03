# Note to the developer — `create_connections` added

## What prompted this

The Scabard API docs at https://www.scabard.com/developer/scabard-api-documentation
gained a new endpoint:

```
POST /api/v0/campaign/{campaign_id}/{concept}/{thing_id}/connect
```

This closes the open question from `CHANGES_CONNTYPES.md` — the `postParam`
field returned by `list_connection_types` is now officially the form key for
the connect endpoint. Documented request shape:

```
-d "mother_of:character=Khal"
-d "home:place=Invidia"
-d "concept=Character"
```

Response (one record per connection, plus `isSuccess`):

```json
{
    "isSuccess": true,
    "mother_of:character": {
        "isFormer": false, "isSecret": false,
        "relId": 16042616, "uri": "/campaign/121/character/162",
        "value": "Khal"
    },
    "home:place": {
        "isFormer": false, "isSecret": false,
        "relId": 16052101, "uri": "/campaign/121/place/158",
        "value": "Invidia"
    }
}
```

Key shape notes:
- The docs example uses form-encoded `-d key=value` pairs, but the live
  server **only accepts JSON** — form requests get `200 {"isSuccess": false}`
  silently. Confirmed against `acquaintance_of:character`, `agent_of:character`,
  `ally_of:character`, `home:place` on 2026-05-10. See open question #1 below.
- Target is identified by **name**, not `thing_id` — the API resolves it
  server-side and returns the canonical `uri`.
- Multiple connections fit in one call. The response uses postParam strings
  as top-level keys alongside `isSuccess`.

## What I changed

### `scabard_client.py`

Added `create_connections(campaign_id, concept, thing_id, connections)`:

```python
def create_connections(self, campaign_id, concept, thing_id,
                       connections: dict[str, str],
                       ) -> tuple[bool, dict[str, dict]]:
    payload = {"concept": concept.title(), **connections}
    result = self._post(
        f"{self.BASE_URL}/campaign/{campaign_id}/{concept}/{thing_id}/connect",
        payload,
    )
    ok = bool(result.get("isSuccess", False))
    records = {k: v for k, v in result.items()
               if k != "isSuccess" and isinstance(v, dict)}
    return ok, records
```

`concept.title()` matches the existing create/update casing rule. The
returned `records` dict separates the connection entries from `isSuccess`
so callers can iterate cleanly.

I initially extended `_post` with a `form=True` kwarg to match the docs
example, but the live endpoint rejected every form-encoded request with
`200 {"isSuccess": false}`. Switching to a plain JSON body (matching every
other POST in the SDK) made it work. Reverted the `_post` change; this
method uses the standard JSON path.

### `test_scabard_api.py`

Split the previous Section 4 (cleanup) into two:

- **Section 4 — Connection creation.** Creates a second test page
  (`TEST SDK Target <ts>`), then picks the first self-referential connection
  type (`source == target == args.concept` Title-cased) from
  `list_connection_types(args.concept)` and posts it. Asserts `isSuccess`,
  the returned postParam record is present, `value` equals the target name,
  `relId` is an int, `uri` is non-empty. Prints the discovered relationship
  for eyeballing. Skips cleanly if no self-referential type exists.
- **Section 5 — Cleanup.** Now marks **both** test pages
  `[TEST PAGE - SAFE TO DELETE]` (previously only the source).

### `SCABARD_SDK.md`

Added a `### create_connections(...)` reference section after
`list_connection_types` (connection-related methods stay grouped). Documents
the postParam→target-name dict, target-resolved-by-name caveat, return
shape table (relId / uri / value / isFormer / isSecret), and an example
that pulls a postParam from `list_connection_types`.

### `CLAUDE.md` (scabard_sdk/)

- Methods table: added `create_connections` row.
- Rewrote bullet 6: `postParam` is no longer a guess — confirmed as the
  endpoint's request key.
- New bullets 7–10 covering: docs/server body-format mismatch (form vs
  JSON), target-by-name resolution, response shape (postParam strings at
  top level next to `isSuccess`), and the now-official canonical concept
  list (which uses `place`, not `location` — flagged for a future
  `scabard_sync.py` pass).

### `__init__.py`

Untouched. The new method is on `ScabardClient`; symbol re-exports already
cover it.

## What I deliberately did not do

- **No update / delete / list-existing-connection wrappers.** Those
  endpoints don't exist yet. The returned `relId` is the only handle, so
  callers persist it if needed.
- **No name → thing_id pre-resolution helper.** The API resolves targets
  server-side; a client-side lookup would just duplicate work. If a target
  doesn't exist, the API tells us (TBD what the failure mode looks like —
  see open questions below).
- **No `isFormer` / `isSecret` request parameters.** They appear in
  responses but the docs don't show how to set them on create. Added as an
  open question rather than a guess.
- **No `scabard_sync.py` extension.** Pushing connections from campaign
  documents is a precision decision (scope, attribution, direction) and per
  the global LLM-pipeline rule needs an extract → human-review → render
  split. That's a separate change.
- **No `location` → `place` rename across the project.** Flagged in
  `CLAUDE.md` so the next `scabard_sync.py`-focused change picks it up; not
  fixed here.

## How to verify

1. Static checks (done):
   ```bash
   python3 -m py_compile scabard_sdk/scabard_client.py scabard_sdk/test_scabard_api.py
   python3 -c "from scabard_sdk import ScabardClient; print(ScabardClient.create_connections.__doc__.splitlines()[0])"
   ```
2. Live smoke (needs fresh credentials — 24-hour key expiry; I have not run
   this yet):
   ```bash
   python3 -m scabard_sdk.test_scabard_api \
       --username "$SCABARD_USER" \
       --access-key "$SCABARD_KEY" \
       --campaign-id "$SCABARD_CAMPAIGN"
   ```
   Look for:
   - `PASS  create_target_page`
   - `PASS  create_connection_between_test_pages`
   - Printed line: `(Character —[<rel>]→ Character, relId=<n>)`
   - Section 5 cleanup marks **both** test pages safe-to-delete.

## Open questions for the API author

1. **Docs/server mismatch on body format — please fix one or the other.**
   The docs example for `/connect` uses `-d key=value` form pairs, but the
   live server only accepts JSON. Every form-encoded request returns
   `200 {"isSuccess": false}` silently — no error message, no hint that the
   format is wrong. I burned about 30 minutes hunting this down (tried raw
   `:` in keys, `%3A`, lowercase vs Title-case concept, thing_id vs name in
   the value, omitting `concept` entirely — all fail identically). Switching
   to a JSON body made it work first try. Either the docs example should
   show a JSON body (matching the existing "Sending JSON" sidebar pattern),
   or the form path should be accepted server-side. **And the silent
   `isSuccess: false` is the real footgun** — please consider returning
   something diagnosable (e.g. `{"isSuccess": false, "error": "..."}`) when
   the body doesn't parse. Right now it's indistinguishable from "target
   not found" or any other failure mode.
2. **Update / delete / list-existing connections.** The /connect endpoint
   creates edges, and the response gives us `relId`, but there's no way to
   delete, update, or enumerate them through the API. Anything planned?
   (For sync use cases this matters — a regenerated extraction needs to be
   able to remove stale edges, not just add new ones.)
3. **Setting `isFormer` and `isSecret` on create.** Both appear in the
   response with default `false`. Is there a way to set them on `/connect`
   (e.g. a nested object value `{"value": "Khal", "isSecret": true}`?), or
   are those only settable via a future update endpoint?
4. **Behaviour when the target name doesn't exist.** Does the endpoint
   return 404, 200 with `isSuccess: false`, or 200 with partial success
   (some keys present, others missing)? Given that bad body shape *also*
   returns `isSuccess: false`, distinguishing these in client code is
   currently impossible.
5. **Duplicate connection posts.** If the same `mother_of:character=Khal`
   is posted twice, do you get two edges (two `relId`s) or is it
   idempotent (one edge, same `relId`)?
6. **Concept casing in the body.** The SDK Title-cases `concept` in the
   body field on `/connect` to match `create_page` / `update_page`. Both
   `"Character"` and `"character"` were accepted in testing — the field may
   not actually be consumed. Worth a docs line either way.

None of this is blocking. The SDK ships today — connection creation works
end-to-end against the documented contract. Flagging the rough edges in case
they're useful when you revisit this area.

— Claude  &  Kostadis
