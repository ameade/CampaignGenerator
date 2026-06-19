# Component: turbovecdb

> An embedded vector DB that can sit *behind* MemPalace as its backend. CG never
> touches it directly — it only ever sees MemPalace. [↑ index](index.md)

**Repos:** `~/src/turbovecdb` (library, branch `re-embed`) and
`~/src/turbovecdb-service` (a thin HTTP wrapper — **not** a git repo) ·
**Deep docs:** `turbovecdb/docs/ARCHITECTURE.md`, `docs/core/data-model.md`, `docs/mempalace-backend-gaps.md`

---

## What it is

A small, embedded, CPU-resident vector database. No server, no daemon — you
`import turbovecdb` into the process. Its design in one breath:

- **SQLite is the source of truth.** Every vector is stored exactly as a float32
  BLOB. The fast ANN index (`index.tvim`, 4-bit quantization via the separate
  `turbovec` library) is a *disposable, rebuildable cache*.
- **Caller brings embeddings** (or an `embedder` callable for lazy embedding).
- **Approximate search, exact re-rank.** The quantized index finds candidates
  fast; results are re-ranked with true cosine distance ∈ [0, 2].
- **Multi-process safe** — file-lock-serialized writers, lock-free readers kept
  coherent by a `store_gen` generation counter.

This is the same "verbatim truth + rebuildable index" philosophy MemPalace
itself follows — which is why it's a natural backend for it.

> The `re-embed` branch adds `Collection.reembed()` (in-place re-embedding, can
> change dimensions) and `Database.delete_collection()`.

---

## Public API

```python
import turbovecdb
db  = turbovecdb.connect("/path/to/db")              # -> Database
col = db.collection("docs", embedder=fn, create=True) # -> Collection
col.add(ids=["a"], documents=["text"], vectors=[[...]])
hits = col.query(vector=[...], k=5)                    # -> QueryResult
```

`Collection`: `add`/`upsert`/`delete`/`query`/`get`/`count`/`close`/`reembed`.
Vectors must be a positive multiple of 8 dims, are L2-normalized on input,
metric is cosine.

Key files: `src/turbovecdb/{__init__,database,collection,index}.py`.

---

## On-disk layout (per collection)

```
<db>/<collection>/
  store.sqlite3        durable source of truth (WAL)
  store.sqlite3-wal/-shm
  index.tvim           turbovec 4-bit index (rebuildable cache)
  write.lock           cross-process write lock
```

`docs` table = id-map + vectors (`uid` INTEGER PK, `str_id` UNIQUE, `document`,
`metadata` JSON, `vector` BLOB). `meta` table holds `dim`, `bit_width`, `metric`,
`next_uid`, `store_gen`, `tvim_gen`.
On read, if `tvim_gen == store_gen` the `.tvim` loads fast; otherwise it rebuilds
from `docs.vector` (sub-second for 10k+ rows).

---

## The seam: how MemPalace consumes it

This is the only path by which turbovecdb enters the campaign system.

1. **Selection** — `MEMPALACE_BACKEND=turbovec` (the env var the precompact hook
   in this repo uses). `mempalace/backends/registry.py` resolves it to a
   `TurboVecBackend` singleton.
2. **Adapter** — `mempalace/backends/turbovec.py`: `TurboVecBackend` implements
   MemPalace's `BaseBackend`; `TurboVecCollection` wraps a turbovecdb
   `Collection` to the `BaseCollection` contract. Mapping: MemPalace's
   `embeddings` key ↔ turbovecdb's `vectors` key; errors bridged
   (`DimensionMismatchError`, …).
3. **Storage** — one DB per palace at `<palace>/turbovec/`.
4. **Lazy embedder** — MemPalace passes a callable that resolves its embedding
   function on first use, so precomputed-vector paths never load a model.

Known contract gaps (turbovecdb is younger than the contract) are tracked in
`turbovecdb/docs/mempalace-backend-gaps.md` — e.g. no stored embedder-identity
guard (GAP-1), `$contains` via table scan not FTS (GAP-2), no atomic
metadata-only update (GAP-3). Several other gaps are *dissolved* by the
source-of-truth + rebuildable-cache design (no corruption-recovery modes needed).

---

## turbovecdb-service (aside, not in the campaign path)

`~/src/turbovecdb-service/service.py` is a ~200-line stdlib `http.server` over
turbovecdb, built for a **different** consumer (llm_wiki duplicate-detection):
`POST /upsert`, `/candidate_pairs`, `/count`, `/clear`, `GET /health`, one fixed
`"pages"` collection per DB. It is **not** how MemPalace talks to turbovecdb
(that's an in-process import, above). Mentioned here only so you don't confuse
the two when you see the service running. It is not under version control.
