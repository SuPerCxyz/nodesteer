# Validation record

Date: 2026-11-05 (Asia/Shanghai)
Change: `refine-node-monitoring-views`
Scope: isolated verification only; no archive, no Git writes, no production service changes.

## Environment

- Hub: `http://127.0.0.1:18830`, Gateway: `127.0.0.1:18445`, isolated data under `/home/superc/tmp/nodesteer-monitoring-e2e/`.
- Agents:
  - Native: `e2e-monitoring-native`, node ID `48969b6d-8c5d-4e06-9c43-1e281a3ece01`, 5s interval.
  - Docker Host Integration with `HostRoot=/`: `e2e-monitoring-hostint`, node ID `605bcc66-e16b-4342-8e07-a60d96ab0f04`, 5s interval.
- Existing preview Hub on `:18810` and its Agents were not started, stopped, modified or used for writes.
- Screenshots and machine-readable results:
  - `/home/superc/tmp/nodesteer-monitoring-e2e/screenshots/dashboard-node-row.png`
  - `/home/superc/tmp/nodesteer-monitoring-e2e/screenshots/node-detail-containers.png`
  - `/home/superc/tmp/nodesteer-monitoring-e2e/screenshots/node-detail-light.png`
  - `/home/superc/tmp/nodesteer-monitoring-e2e/screenshots/node-detail-dark.png`
  - `/home/superc/tmp/nodesteer-monitoring-e2e/screenshots/node-detail-tooltip.png`
  - `/home/superc/tmp/nodesteer-monitoring-e2e/screenshots/node-detail-mobile.png`
  - `/home/superc/tmp/nodesteer-monitoring-e2e/e2e-result.json`

## Real Agent → Hub → API → page evidence

- Inventory hostname: `aitools.soocoo.xyz`, from `/proc/sys/kernel/hostname`; configured node name remains `e2e-monitoring-native` and is not used as the inventory hostname.
- Host facts: Ubuntu 26.04 LTS, kernel 7.0.0-38-generic, Intel i5-12400, memory 15.1 GiB, uptime ~6d16h.
- Filesystem: 4 sampled mounts (`/`, `/data/media`, `/data/onedrive`, `/home/superc/tmp`) with paired `mount+device` series; root used `127.3 GiB` of `250.9 GiB`.
- Block devices: 20 entries classified as `disk`/`partition` from sysfs. IOPS cards rendered only for whole `vda`; `vda1`/`vda2` were excluded.
- Containers: discovered 3, sampled 2, limit 100, same observation timestamp → UI correctly displayed partial completeness. Each sampled container had an independent curve; stable-ID filter hid the other container without refetching history (cache reuse).
- Ranges: switching to 3h and 12h produced history requests with 3h/12h windows.
- Chart tooltip: hover showed `总使用率 14.7%` from the real CPU series.
- Navigation: Dashboard node row click navigated to `/monitoring/nodes/{id}`.
- Responsive: 390px viewport had `scrollWidth == clientWidth == 390`; no horizontal overflow.
- Browser console errors: none.
- Host Integration root `/` produced the same inventory hostname, OS, kernel, filesystem count and block-device classification as native mode.

## Automated verification

- `TMPDIR=/home/superc/tmp-nodesteer-regression make test`: passed.
- `TMPDIR=/home/superc/tmp-nodesteer-regression make lint`: passed.
- `go test -buildvcs=false -count=1 ./internal/agent ./internal/protocol ./internal/store ./internal/monitoring ./internal/hub ./internal/hub/api ./internal/hubserver`: passed.
- `go test -race -buildvcs=false -count=1 ./internal/agent ./internal/store ./internal/monitoring ./internal/hub ./internal/hub/api`: passed.
- `cd web && npm test -- --run`: 78/78 files passed, 0 retries.
- `cd web && npm run lint`: 0 errors, 12 existing warnings.
- `cd web && npm run build`: passed.
- `openspec validate refine-node-monitoring-views --strict`: valid.
- Targeted boundary coverage includes filesystem Bfree/Bavail, memory/free baselines, sysfs relative links/hotplug/inaccessible data, container limit+1 and first-sample baselines, 500-sample completeness batching, 2000-series transaction rollback, latest missing/stale/future/invalid handling, queue concurrency/cancellation, and range/header/container UI behavior.

## `disk I/O error (778)` investigation

- `/tmp` was 80% full (6.1 GiB of 7.6 GiB) at the time.
- Linking a Hub binary directly onto the NFS-backed `/home/superc/tmp` mount failed with `mapping output file failed: disk quota exceeded`; writing the binary to the local root filesystem succeeded.
- The isolated Hub database on `/home/superc/tmp/nodesteer-monitoring-e2e/data/hub.db` passed `PRAGMA integrity_check` (`ok`) and produced no SQLite/I/O errors during ingest and page verification.
- Conclusion: observed failure was environmental storage/quota pressure, not an application code defect. Workaround used local build output plus isolated `/home/superc/tmp/...` data/TMPDIR.

## Limitations

- One native and one Host Integration Agent only; not a 100+ node scale test.
- Container limit+1 (101) was exercised by fixtures/unit tests, not by creating 101 real containers.
- Real block-device hotplug was not performed on the host; hotplug, relative sysfs links and inaccessible sysfs are covered by isolated Go tests.
- Production deployment, license and upgrade rollback were not exercised.
