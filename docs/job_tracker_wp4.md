# WP4 — strict collection and engineering verdicts

Implemented in flowkit on 16 September 2026. Collection now distinguishes three
questions: did the intended job execute, are all required artifact bytes verified,
and does a valid engineering report pass the declared checks? No consumer or
installed harness configuration was changed. Licensed-tool report adapters and
live activation remain rollout work.

## Collection contract

`collect` first reads terminal tracker state and the result for the same job,
requires complete stamped artifact coverage, and invokes the existing `verify`.
Empty requirements, missing files, partial hashes and `UNSTAMPED` are not accepted.
The existing human tracker CLI remains compatible.

The new shipped `jobs/bin/evidence.py` then independently checks metadata's job,
workspace and expected-artifact list, terminal status/exit-code consistency, and
each artifact's size and SHA-256 against the result stamp. It rejects symlink
artifacts, including symlinked subdirectories. It reads and hashes the report in
one pass and parses **those same verified bytes**. Reports are limited to 1 MiB;
other files to 100 MiB, consistent with the tracker's limited stamping scope.
Missing Python, unreadable records, size limits and transport failures never
become accepted evidence. This is a point-in-time check, not immutable storage.

The helper runs through `Transport` and its existing content-hashed bundle
installer. It returns verdicts, issue codes, counts and configured failed/missing
check names only. Report bytes, logs, arbitrary report fields and process values
are not returned to the assistant. Durable collection writes private
`collection.json` and `collection.md` beside `task.json`, with links between them.
The Markdown summary records job/host, execution, artifact verdict, engineering
verdict, failed/missing checks, issues, next action and remote record location.
Individual files are atomically replaced; they are not a multi-file transaction.
The JSON result is the structured snapshot; repeat collection for current evidence.

## Engineering profile and report format

Set `engineering_report` to null to collect artifact evidence without an engineering
verdict. Otherwise use the only supported parser, `json-v1`:

```json
{
  "parser": "json-v1",
  "path": "report.json",
  "design": "demo",
  "top": "top",
  "checks": ["timing", "drc"],
  "corners": ["tt", "ss"]
}
```

The report path must also appear in `expected_artifacts`. The check/corner lists
must be nonempty and unique. Every check must appear at every configured corner;
use explicit subsets in separate profiles if a different coverage policy is
needed. Unknown parser names are refused before launch; no arbitrary plugin
imports or report-provided code execute.

A report is a UTF-8 JSON object with **exactly** these fields:

```json
{
  "schema": 1,
  "job_id": "synthetic-run-example",
  "repository": "flowkit-synthetic",
  "design": "demo",
  "top": "top",
  "manifest_sha256": "<64 lowercase hex digits>",
  "request_sha256": "<64 lowercase hex digits>",
  "source_sha256": "<64 lowercase hex digits>",
  "checks": [
    {"name": "timing", "corner": "tt", "status": "pass"},
    {"name": "timing", "corner": "ss", "status": "fail"},
    {"name": "drc", "corner": "tt", "status": "pass"},
    {"name": "drc", "corner": "ss", "status": "pass"}
  ]
}
```

Duplicate JSON keys, nonfinite values, unknown fields, duplicate checks, extra or
missing corners, and statuses other than `pass`/`fail` are invalid. An overall
report-provided success flag is not accepted: the collector calculates its verdict.
The design/top come from the profile; the repository/job from the reference;
the input-manifest, request and source digests from the durable task record.
`source_sha256` is the canonical JSON digest of WP3's entire source-identity object.
Collection through a legacy reference without durable input identity cannot
produce an engineering pass.

For report-producing durable jobs, the payload receives:

- Existing `ASICJOBS_ID` / `ASICJOBS_JOBDIR` from the tracker.
- `ASICJOBS_EXPECTED_MANIFEST_SHA256`.
- `ASICJOBS_EXPECTED_REQUEST_SHA256`.
- `ASICJOBS_EXPECTED_SOURCE_SHA256`.

These three digests declare expectations; they **do not measure actual inputs**.
A trusted process-specific adapter must verify the inputs it actually consumed,
resolve tool results and thresholds, then produce this normalized report. Simply
echoing expected identities and writing `pass` is not an engineering check.
The synthetic tests do that only to exercise the contract. This shared milestone
does not implement Cadence/Calibre parsers, stage a frozen checkout or attest remote
source bytes. Such adapters must be validated with their consumers during WP5.

## Verdicts

| Situation | Artifact evidence | Engineering |
|---|---|---|
| All bytes and identity/coverage valid, all checks pass, execution succeeds | tracker-verified | pass |
| Valid complete report has failing checks, even if the process exits zero | tracker-verified | fail |
| Report claims all checks pass but execution failed | tracker-verified | invalid |
| Wrong design/top/job/input or incomplete/duplicate checks | tracker-verified if bytes match | invalid |
| No parser or no durable input identity | tracker-verified if bytes match | unchecked |
| Missing/unstamped required file | incomplete | unchecked |
| Changed bytes, workspace mismatch, symlink or failed reader | unverified | unchecked |

`report-result` permits reporting a **pass or fail** within the declared contract;
it does not mean success. Invalid/unchecked results require evidence inspection.
CLI exit zero continues to mean a known observation, not a passing design. Use the
JSON verdict fields. Harness instructions now say this explicitly instead of
always describing engineering as unchecked.

## Tests and compatibility

```sh
python3 -m unittest jobs.test_evidence jobs.test_workflow jobs.test_state \
  integrations.cluster_jobs.test_hooks conformance.test_jobs_distribution -v
python3 jobs/test_remote.py
```

Fixtures cover passing/failing checks, all identity fields, missing/duplicate/extra
checks, wrong corners, malformed/duplicate/nonfinite JSON, oversize reports,
unstamped/stale/subset artifacts, wrong workspaces, symlinks, missing durable
identity and unsuccessful execution. Actual local POSIX jobs produce pass/fail
reports, run through durable collection and generate reviewable evidence summaries.

Strict metadata parsing exposed an existing launcher defect: command arguments
with control characters/newlines produced invalid JSON in `meta.json`. `runjob`
now escapes those characters using POSIX awk, including trailing newlines. The
existing literal-parameter job tests and transport regression suite exercise it.
No change was made to payload argument semantics. The remote regression suite
passes 94 checks with the extended bundle; vendoring includes the new helper.

Validation: 47 combined tests and one additional verifier-failure regression
passed under WSL. Native Windows passed the corresponding 38 tests with 10
POSIX-only cases skipped. Python 3.6 grammar checks and
`git diff --check` also passed.

This completes the shared WP4 contract and synthetic validation. An engineering
pass is only as reliable as the trusted report producer and declared coverage.
WP5 still needs process-adapter compatibility, rollout packaging and separately
authorized consumer/harness activation with representative tools.
