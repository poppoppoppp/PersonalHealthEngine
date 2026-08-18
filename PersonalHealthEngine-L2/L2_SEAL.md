# Personal Health Engine
# Layer 2 Seal

Status: SEALED
Sealed at UTC: 2026-08-16T04:14:57.668492+00:00

## Final Decision

L2 CORE ACCEPTANCE = PASS

Layer 2 has demonstrated durable, append-preserving,
version-aware and provenance-complete storage of
Xiaomi raw health records.

## Canonical Layer 2

- Root: `D:\PersonalHealthEngine-L2`
- Database: `D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3`
- Archive: `D:\PersonalHealthEngine-L2\archive`
- Production schema version: `1`
- Logical identity version: `xiaomi-v0.1`

## Frozen Logical Identity

`provider + region + dataset + raw_record.key + raw_record.sid + raw_record.time`

`update_time`, `value`, timezone fields and all other payload content
do not participate in logical identity.

## Final Raw Store Counts

- captures: 9
- source artifacts: 81
- logical records: 5030
- raw record versions: 5297
- observations: 15751
- revisions: 267
- late arrivals under L2 semantics: 56

## Validated

- SQLite integrity: PASS
- Foreign keys: PASS
- WAL journal mode: PASS
- Layer boundary / table set: PASS
- Production schema version: PASS
- Count captures: PASS
- Count source_artifacts: PASS
- Count logical_records: PASS
- Count raw_record_versions: PASS
- Count raw_record_observations: PASS
- Ingestion run audit: PASS
- Ingestion issues: PASS
- Credential isolation: PASS
- Raw SID preservation: PASS
- Sleep source coexistence: PASS
- Version classification invariant: PASS
- Revision preservation: PASS
- Late-arrival semantics: PASS
- Full provenance traceability: PASS
- Backup / restore: PASS
- Full rebuild from archive: PASS
- Atomic schema migration: PASS

## Layer Boundary

Layer 2 stores raw / near-raw health facts and provenance only.
It does not perform feature engineering, daily aggregation,
sleep-source selection, personal baseline calculation,
anomaly detection, health scoring or AI reasoning.

## Recovery Contract

- SQLite backup provides fast recovery.
- Immutable L2 archive is the source of full rebuild.
- Raw Store semantic state has been rebuilt successfully from archive alone.

## Upstream Boundary

Layer 1 remains SEALED.
Layer 2 does not reopen Xiaomi authentication, protocol or Collector logic.

## Next Layer

Next formal work may proceed to:

Layer 3 = Feature Engineering / 特征工程

Do not modify the frozen Layer 2 raw identity/version semantics
without an explicit migration and compatibility review.
