## 1. Agent and contracts

- [x] 1.1 Collect filesystem capacity and free memory while preserving old metrics; cover boundaries.
- [x] 1.2 Classify and refresh block devices and actual host hostname through inventory JSON and both protocol conversions; test compatibility and hotplug.
- [x] 1.3 Collect container IDs and same-timestamp completeness gauges with bounded enumeration and meaningful edge tests.
- [x] 1.4 Ensure Hub ingest, latest, storage and units preserve new metrics and dimensions.

## 2. Monitoring UI

- [x] 2.1 Add ranges and accessible whole-row navigation, preserving links, selection and modifiers.
- [x] 2.2 Update node header, metadata and CPU, memory, filesystem and IOPS views.
- [x] 2.3 Display independent container series, completeness, stable ID filtering and legacy identities.
- [x] 2.4 Implement bounded cancellable series loading, cache reuse, independent errors and retry.

## 3. Validation and documentation

- [x] 3.1 Update directly affected product and architecture documentation.
- [x] 3.2 Run relevant Go tests, Vitest, typecheck, lint, build, strict OpenSpec validation and diff checks.
- [x] 3.3 Validate real isolated Agent→Hub→API→charts, responsive behavior and review; record limitations.
- [x] 3.4 Verify host-root sysfs relative links, inaccessible data, hotplug and matching Host Integration root.
- [x] 3.5 Verify atomic completeness across 500-sample batches, latest ordering/missing/stale/future/invalid data and 2000-series write failure without changing the limit.
- [x] 3.6 Capture isolated real Agent → Hub → API → page interactions and report actual data, UI feedback and any failures separately from fixtures.
