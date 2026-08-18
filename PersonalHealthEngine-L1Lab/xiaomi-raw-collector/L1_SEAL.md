# Personal Health Engine
# Layer 1 Final Seal

Status: SEALED
Date: 2026-08-16
Collector: Xiaomi Raw Collector v0.3.1

## Final Decision

L1 CORE ACCEPTANCE = PASS

Layer 1 has demonstrated stable, independent, unattended acquisition
of Xiaomi Mi Fitness China cloud health data across multiple real-world
wear days.

## Validated

- Xiaomi CN cloud authentication
- Independent Xiaomi protocol implementation
- Pagination
- Raw payload preservation
- Source SID preservation
- Incremental acquisition
- 2-day overlap acquisition
- Late-arriving record detection
- Revision detection
- Missing record detection
- Duplicate audit
- Persistent collector state
- Windows unattended scheduled execution
- Multi-day continuous real-world acquisition

## Validated datasets

- steps
- calories
- heart_rate
- resting_heart_rate
- sleep
- spo2
- stress

## Supported but sparse / deferred

- abnormal_heart_beat: endpoint supported, no positive sample observed
- sport_records: endpoint supported, positive workout sample deferred

## Final multi-day audit

Late-arriving overlap records: 1837
Revised overlap records: 267
Missing overlap records: 0

The observed late-arrival and revision behavior confirms that overlap
re-fetching is required. Timestamp-only forward sync is not sufficient.

## Freeze

Xiaomi Raw Collector v0.3.1 is the canonical Layer 1 collector baseline.

Further schema/storage/normalization work belongs to Layer 2 or later layers.
