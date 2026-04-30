# CLAUDE.md — scabard_sdk

Python SDK for the [Scabard](https://www.scabard.com) world-building tool REST API.

## Structure

```
scabard_sdk/
  __init__.py          # re-exports all public symbols
  scabard_client.py    # ScabardClient class + exceptions
  test_scabard_api.py  # integration test script (standalone, requires live credentials)
  SCABARD_SDK.md       # user-facing documentation
  CLAUDE.md            # this file
```

## Usage

```python
from scabard_sdk import ScabardClient, ScabardAuthError

client = ScabardClient(username="you", access_key="your-key")
campaigns = client.list_campaigns()
ok, thing_id = client.create_page(campaign_id=121, concept="character", name="Grundar")
```

Run tests from the **project root**:
```bash
python -m scabard_sdk.test_scabard_api --username <u> --access-key <k> --campaign-id <id>
```

## ScabardClient methods

| Method | Returns | Description |
|---|---|---|
| `list_campaigns()` | `list[dict]` | All campaigns the user is GM of |
| `get_campaign(campaign_id)` | `dict` | Campaign details (`main`) + page summaries (`rows`) |
| `list_pages(campaign_id, concept)` | `list[dict]` | All pages of a concept |
| `get_page(campaign_id, concept, thing_id)` | `dict` | Single page details |
| `fetch_existing(campaign_id, concept)` | `dict[str, int]` | `{name: thing_id}` map for all pages |
| `create_page(campaign_id, concept, name, ...)` | `tuple[bool, int \| None]` | Create page; re-fetches list to discover new ID |
| `update_page(campaign_id, concept, thing_id, name, ...)` | `bool` | Update existing page |
| `list_connection_types(concept)` | `list[dict]` | Catalog of valid connection types for a concept (campaign-agnostic) |

`create_page` and `update_page` share the same optional keyword fields:
`brief_summary`, `description`, `secrets`, `gm_secrets`, `is_secret`.

## Exceptions

All inherit from `ScabardError(Exception)`. Each has `.status_code` and `.detail` attributes.

| Exception | HTTP | Cause |
|---|---|---|
| `ScabardAuthError` | 401 | Bad or expired credentials |
| `ScabardForbiddenError` | 403 | No access to the resource |
| `ScabardNotFoundError` | 404 | Resource does not exist |
| `ScabardRateLimitError` | 429 | Exhausted after 4 retries |
| `ScabardError` | 5xx | Server error (base class) |

## Undocumented API behaviours (discovered via integration testing)

These differ from what the official API docs describe — handle them accordingly:

1. **Invalid campaign IDs return 500**, not 404/403. The SDK wraps all 5xx as `ScabardError`.
2. **List endpoint returns `"uri"` not `"id"`**. `fetch_existing` parses the thing_id from the URI path (`/campaign/{id}/{concept}/{thing_id}`).
3. **Concept casing rule: URLs are lowercase, data values are Title Case.** URL path segments use lowercase (`/campaign/{id}/character`); response bodies and request body fields use Title Case (`"concept": "Character"`, `source: "Place"`, `target: "Event"`). The SDK applies `.title()` to body `concept` fields automatically.
4. **Create response does not return the new page's ID.** `create_page` re-fetches the page list after a 1-second pause to discover the ID by name match.
5. **Connection-type entries always include `isSymmetric` in live responses.** The docs example shows entries without the field, but live testing across `character`/`place`/`event`/`group` (226 entries total) had `isSymmetric` on every entry. The SDK still defensively treats absence as `False` (`.get("isSymmetric", False)`) — harmless, in case the docs example reflects a real edge case.
6. **Connection-type entries include an undocumented `postParam` field.** Every live entry has e.g. `"postParam": "acquaintance_of:character"` alongside the documented keys. The format (`{snake_rel}:{lowercase_target}`) looks like the parameter token a future connection-CRUD endpoint would accept. The SDK passes it through in `list_connection_types` but does not consume it — no connection CRUD wrapper exists because the endpoint isn't documented. Worth asking the API author about before guessing.

## Rate limiting

`_post` retries on 429 with exponential backoff: 4 attempts, waits of 5s / 10s / 20s / 40s. Raises `ScabardRateLimitError` if all attempts fail.

For bulk operations, add `time.sleep(0.4)` between calls (as `scabard_sync.py` does).

## Authentication

- Headers: `username` + `accessKey`
- Keys expire **24 hours** after generation
- Generated at: `https://www.scabard.com/pbs/<username>` → down-arrow → API Access Key

## Adding new methods

All HTTP calls must go through `_get()` or `_post()` — never call `requests` directly in public methods. This ensures consistent error handling and rate-limit retry across all endpoints.
