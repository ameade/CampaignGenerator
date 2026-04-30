# Note to the developer — `list_connection_types` added

## What prompted this

The Scabard API docs at https://www.scabard.com/developer/scabard-api-documentation
gained a new endpoint:

```
GET /api/v0/campaign/conntypes/{concept}
```

It returns the catalog of valid connection (relationship) types for a concept.
Unlike every other endpoint in the API, it does **not** take a `campaign_id` —
connection types are global. Response shape:

```json
{"connTypes": [
  {"isSymmetric": false, "rel": "Setting of", "source": "Place", "target": "Event"},
  {"rel": "Setting of", "source": "Place", "target": "Adventure"},
  {"rel": "Steward",    "source": "Place", "target": "Character"}
]}
```

Note `isSymmetric` is present on some entries and absent on others. We treat
absence as `False` rather than KeyErroring.

## What I changed

### `scabard_client.py`
Added one new method, `ScabardClient.list_connection_types(concept)`. It is a
thin wrapper over the existing `_get()` helper, so 401/403/404/429/5xx all
flow through the existing typed exceptions and the 429 backoff still applies.
No changes to any other method, no changes to constructor, no new dependency.

```python
def list_connection_types(self, concept: str) -> list[dict]:
    data = self._get(f"{self.BASE_URL}/campaign/conntypes/{concept}")
    return data.get("connTypes", [])
```

### `test_scabard_api.py`
Added `test_list_connection_types` to Section 2 (campaign access). It calls
the new endpoint with `--concept` (default `character`), asserts the response
is a non-empty list, and verifies each entry has `rel` / `source` / `target`.
Prints a one-line sample so you can eyeball the shape on a live run.

### `SCABARD_SDK.md`
New `### list_connection_types(concept) → list[dict]` section after
`fetch_existing` (read-only methods grouped). Documents the no-`campaign_id`
quirk and the possibly-missing `isSymmetric` field.

### `CLAUDE.md` (scabard_sdk/)
- Added the method to the methods table.
- Added bullet 5 to the "Undocumented API behaviours" list noting that
  `isSymmetric` may be omitted; treat missing as `False`.

### `__init__.py`
Untouched. It only re-exports class symbols; method-level additions don't
need to be re-exported.

## What I deliberately did not do

- **No higher-level helpers.** I did not add `is_valid_connection(source,
  target, rel)` or any caching layer. The docs only expose a list endpoint;
  validation belongs to whatever code actually wants to post connections, and
  there is no connection-CRUD endpoint yet.
- **No connection-CRUD wrappers.** The docs do not (yet) describe how to
  create / update / delete connections between pages, only how to discover the
  type catalog. Adding optimistic guesses would be premature.
- **No re-attempt to canonicalize concept casing.** The URL convention for
  every other GET in the SDK is lowercase; this one matches.

## How to verify

1. Static check (already done):
   ```bash
   python -c "from scabard_sdk import ScabardClient; print(ScabardClient.list_connection_types.__doc__.splitlines()[0])"
   python -m py_compile scabard_sdk/scabard_client.py scabard_sdk/test_scabard_api.py
   ```
2. Live smoke (needs creds — I did not run this):
   ```bash
   python -m scabard_sdk.test_scabard_api \
       --username "$SCABARD_USER" \
       --access-key "$SCABARD_KEY" \
       --campaign-id "$SCABARD_CAMPAIGN"
   ```
   Look for `PASS  list_connection_types` and a printed sample edge.

## Open question for you

What does the server do for an invalid/unknown concept (e.g.
`list_connection_types("not-a-concept")`)? I did not test this against a live
endpoint. If it 404s, `ScabardNotFoundError` will be raised by `_get()`
automatically — fine. If it 200s with `{"connTypes": []}`, we silently return
`[]`. Either is acceptable; if the project ends up caring, document the
observed behaviour in `CLAUDE.md`'s "Undocumented API behaviours" section.
