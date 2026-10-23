# Release checks

Before each release, require a passing GitHub Actions
[`CI` workflow](../.github/workflows/ci.yaml) for the commit being deployed. Its
`Checks and production image` job checks lint/formatting, schema drift, all
PostgreSQL regressions, production configuration and the runtime image.
Production startup refuses development mode, missing or weak secrets, SQLite,
disabled scanning/mail/sandbox controls, untrusted forwarded-header configuration,
or missing filesystem confinement. Deploy with the supplied Compose file and
apply migrations before replacing web and worker processes.

## Changes covered by the readiness review

| Improvement | Enforcement | Regression evidence |
| --- | --- | --- |
| Accepted work progresses during uploads | FIFO acquisition across ready job kinds; cumulative draft/network work budgets and bounded backlog | Replenished validation queue and upload replacement tests |
| Derived files preserve storage capacity | Locked admission, separate workspace/output reservations, execution locks, 10 MB normalized-file cap, image resizing, bounded tmpfs | Amplification, output-limit, crash recovery, low persistent space and competing-draft tests |
| Production settings fail closed | Explicit development opt-in and production startup/deploy checks | Unsafe-mode configuration subprocess tests |
| Errors are observable without financial content | Generated request IDs, safe HTTP/access logs, redacted exception stacks and readiness checks | Real HTTP 500 with private message/path/header assertions; dead-worker readiness test |
| Validation errors remain actionable | Visible field summary, details-step restoration, focused invalid field and renewed acknowledgments | Mobile browser IBAN/date rejection cases |
| Polling survives transient faults | Request deadlines, bounded backoff/jitter and reusable pending upload handles | Upload/signature and receipt-status browser fault tests |
| Maintenance reaches later records | Rotating bounded reconciliation batches and streaming/filterable status output | More-than-50/100-record tests and provider-failure exit status |
| Parsers have limited authority | Landlock file allowlist, seccomp network/process restrictions, no inherited secrets and process-group cleanup | Real confined subprocess, native document pipeline and startup self-test |
| Capacity is measured | Disposable 250-participant HTTP workload with production web/worker resource limits | `scripts/capacity.sh`, private JSON resource, storage and restore evidence |

## Capacity and restore evidence

Run `CAPACITY_OUTPUT_DIR=/absolute/private/evidence ./scripts/capacity.sh 250`.
Set `CAPACITY_PAUSE_SECONDS=10` to include a worker pause. The workload uses two
web processes, one worker limited to two CPUs and 2 GB, 250 separate browser
sessions, two synthetic receipt PNGs and one signature per participant. It exercises
750 real validation jobs, 250 PDF renders and local email captures. Malware
scanning and the external email provider are excluded, so their latency and
outage behavior require staging evidence. The regression suite separately covers
scanner backoff and unknown-send handling.

The default local capacity target is a p95 end-to-end time below 15 minutes with
zero failures. Record the image ID, host/kernel and run conditions. Keep the JSON
results with the release evidence; performance varies with deployment hardware.
If the target fails, inspect queue age, resource use and request errors before
changing limits or worker count.

After intake completes, the script verifies file hashes, stops processing, dumps
PostgreSQL and copies its matching private files. It restores both into a separate
disposable database/volume and verifies integrity and successful content deletion.
The restore probe refuses non-synthetic data. The script removes its temporary
databases/volumes on exit and retains evidence in a private host directory.
This is an automated restore/deletion exercise with synthetic data. An encrypted
off-host production backup, retention and key custody still need deployment evidence.

### Recorded local verification (21 October 2026)

| Check | Result |
| --- | --- |
| Two 250-participant bursts, each with a 10-second worker pause | 250/250 completions and zero failures in each run; p95 totals 499.862 s and 524.520 s |
| Second-run worker cgroup | 140.5 MiB peak memory under the 2 GB limit; zero OOM events |
| Second-run storage and isolated restore | 1,750 files verified; 250 claims restored and purged; zero integrity/deletion failures |
| Final PostgreSQL CI | 140 tests passed; lint, formatting, schema drift, production startup checks and runtime image build passed |
| Browser checks | Desktop/mobile completion, visible IBAN/date errors, transient polling, repeated signature replacement and obsolete-signature rejection passed |
| Maximum document | Confined assembly retained all 100 receipt pages plus the summary within the 10 MB cap |
| High-detail PDF | Two temporary page JPEGs above 10 MB reached compression; all receipt pages and the summary fit below 1 MB, with the aggregate raster budget enforced |

The burst used Linux `7.0.0-38-generic`, x86_64, with image
`sha256:10318adc051d42b8d437d4a3a21ed4bc917a4b32885bcf2da3b2f453c9003f20`.
After the independent review tightened process-control syscall restrictions and
signature recovery, a separate one-participant HTTP/restore/deletion run and the
full CI/browser regressions passed. These results establish local capacity;
repeat the workload on the selected deployment hardware with staging dependencies.

PR review regressions also cover scheduled-retry readiness, recovery from failed
sandbox probes, abandoned workspaces, inherited execution locks after supervisor
SIGKILL and migration of existing output reservations. A separate HTTP run with
1,153,257,472 bytes free on the output filesystem and a distinct 700 MiB workspace
completed successfully and verified all seven files. This exercises rendering
below the previous incorrect 600 MB persistent-workspace threshold.

## Required staging and owner evidence

- Finance approves each form's accounting values, the handoff, the privacy notice,
  the retention periods (PDF 7 days after delivery, content 30 days after
  submission) and handling of unresolved claims. Then set `CONTENT_PURGE_ENABLED=1`.
- The data protection owner approves the privacy notice in the admin area,
  including the named processors and hosting location.
- PT Login: the client, its groups mapper and both portal groups exist; a member
  of each group can sign in and sees only what their role permits.
- A synthetic claim reaches the approved MailerSend/Moss destination. Inspect all
  pages and the signature, exercise signed callbacks/reconciliation and record the
  result. Provider acceptance and recipient delivery remain distinct states.
- An encrypted off-host backup restores database and private files together in
  an isolated environment. Run `verify_storage`, overdue deletion and unknown-send
  review before starting processing. Record the accepted loss/recovery targets.
- Independent PT monitoring alerts on failed HTTP requests, dead workers/scanners,
  old jobs, full storage, purge failures and reconciliation nonzero exit status.
  Verify a delivery-provider outage cannot suppress that monitoring channel.

Record the responsible owner and approval/evidence location in the deployment
record. These approvals and external-system checks cannot be inferred from a
passing local test run or a merged PR.
