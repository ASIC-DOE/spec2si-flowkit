<!--docmeta
title: The housekeeper — classifying what a cluster home may forget
genre: reference
status: active
area: top
owner: soumyajit
updated: 2026-08-26
summary: The vendored cleanup kit (housekeeping/) and the contract it implements — features, decision and mover kept as three separate things so the decision can be evaluated against labels instead of asserted. Measured 2026-08-26 on the BNL home: 468 run dirs (325 untouched >14d), 36 GB of digital-flow trees never walked at all, 334 loose files in $HOME and no crontab, so nothing had ever cleaned up anything; disk was never the constraint (37 TB free of 73 TB), a findable working set was. Records why age alone is a one-feature classifier with an unvalidated threshold and errs both ways (88 of 319 "stale" dirs held signoff artifacts, while drc_v1..drc_v15 read fresh and drc_v2_ant was obsolete at 2.3 days), the four artifact classes and the cascade over them, the classes that are never CANDIDATES so they cannot be selected (output/, reports/, *.db, *.odb — a P&R tree keeps its stage checkpoints on purpose), the two guards that run before age gets a vote on a loose script (git-tracked, referenced by a login file), why the rules are deterministic rather than learned, and how the enforcement is itself made observable. Current status: the operating point is argued and instrumented but NOT yet validated — no labels exist, and housekeeping/stale_labels.sample.json is the harness for producing them.
-->

# The housekeeper

`housekeeping/` is vendored into every consumer the way `routekit/` and
`irdrop/` are. It touches no PDK: mtimes and filenames in, `KEEP` or
`SWEEP` out.

| file | what it is |
|---|---|
| `run_features.py` | extracts per-item features on the cluster. Decides nothing, moves nothing |
| `stale_classify.py` | the decision. One ordered cascade, every row reported with the rule that fired |
| `attic_sweep.sh` | moves what the classifier selects, to `~/.attic/<date>/` |
| `stale_labels.sample.json` | stratified rows awaiting labels, for the confusion matrix |

Installing to `~/bin`, the crontab entry and the liveness warning stay in
each consumer's `deployment/` — those are site concerns; these four files
are the logic.

## 1. What was measured

Nothing had ever cleaned up anything: no crontab, on a home holding **468
run dirs** across five project trees and **334 loose files** in `$HOME`.
Two whole trees (`LDRD_2022`, `LDRD_2025`) were not even enumerated, having
no `analog/work` to glob — and `LDRD_2025/dig_flows` is **36 GB**, seventy
times every analog tree together.

Disk was never the problem: 37 TB free of 73 TB. The goal is a working set
someone can read, which is why nothing is deleted for 90 days.

## 2. Age is a classifier, and an unmeasured one

The first version swept anything untouched for 14 days. That is one
feature and a threshold nobody validated, and against the real tree it errs
in both directions:

- **False stale** — 88 of the 319 it selected held signoff artifacts. A
  signed-off result untouched for a month is *finished*, not stale.
- **False fresh** — `drc_v1`..`drc_v16`. Fifteen were garbage the moment
  the next completed: **superseded, not old**. The sharpest case is
  `drc_v2_ant`, obsolete at **2.3 days** because `drc_v2_ant2` finished a
  day later; age would have protected it for another twelve.

Staleness is a property of the *relationship* between an artifact and what
still depends on it, and an mtime cannot observe a relationship.

## 3. Four classes, one cascade

1415 candidate rows on the measured tree, 977 selected.

| `kind` | rows | what makes it stale |
|---|---|---|
| `run_dir` | 468 | superseded by a later **completed** sibling; else no result and old |
| `dig_scratch` | 610 | `innovus_temp_*`, `.GenusRestruct_*`, numbered `innovus.log*` |
| `home_loose` | 273 | loose in `$HOME`, never a dotfile |
| `proj_script` | 64 | a loose script one level into a project |

The cascade is ordered, first match wins, and the order *is* the operating
point: `.keep` → class-specific rules → empty → crashed → superseded →
holds-a-result → age → keep. Sweeping a result is expensive; leaving junk
costs only tidiness, so the default when nothing fires is `KEEP`.

Completion is **per tool**, and knowing one tool under-detects it badly: a
version that knew only Calibre's `DRC.rep` scored 97 of 468 runs complete
where 254 were, and supersession — which needs a completed successor —
found 13 where it should find 41. Pegasus writes `<cell>_drc.sum`, strmout
writes `strmout.log`, Virtuoso leaves `worker.raw.txt`.

## 4. What can never be selected

⚠️ **A DIGITAL FLOW IS NOT A RUN DIR AND MUST NOT MEET ITS RULES.** A P&R
tree holds every stage of one run and keeps its checkpoints *on purpose* —
restarting from `place.odb` after routing fails is the normal way to work.
"Superseded by a later stage" is true there and catastrophic.

So `output/`, `reports/`, `*.db` and `*.odb` are **never enumerated as
candidates at all**. A class that cannot appear on the list cannot be
selected off it, which is a stronger guarantee than a rule that decides to
spare them — and the precedent is on record: a `--delete` push already
removed `init.odb` and `fplan.odb` from a run that had just produced them.

⚠️ **AND A LOOSE `.sh` MAY BE SOURCE.** One consumer holds 13 git-tracked
shell scripts indistinguishable by age from scratch. Two guards run before
age gets a vote: **tracked by git**, and **named by a login file or the
crontab**. Measured: 9 tracked and 4 referenced found, 0 of either
selected, 0 of 977 selections in a protected digital class.

## 5. Why the rules are deterministic and not learned

It is a binary classification problem, and naming it that is what exposed
the defect — but a learned model is the wrong tool here, for reasons about
this problem rather than about taste:

1. **There are no labels.** n=1415 with zero ground truth; anyone able to
   label enough to train has already done the work by hand.
2. **The decisive features are facts, not evidence.** "A later run of the
   same base name completed" is read off the disk. Fitting a model over a
   fact you can read is how a check goes vacuous.
3. **The cost asymmetry is extreme, known in advance, and not what
   accuracy optimises.** The operating point *is* the design.
4. **It must explain itself.** "superseded by `drc_v16`" is actionable at
   03:00; `p(stale)=0.73` is not.

What the framing does buy, and what the kit implements: an explicit feature
set, a stated operating point, a labelled evaluation and a confusion matrix
that says what the rule costs. `--sample` emits stratified rows, `--labels`
scores the rules against the age-only baseline with *wrongly-swept* broken
out — the only column that costs anything. If labelling shows a wide grey
band, fit something over these same features; the extractor is deliberately
independent of the decision so that stays possible.

## 6. Enforcement, and watching the enforcement

A script nobody runs is not a policy. `cron` is the mechanism, because it
is the only thing that runs with nobody present.

⚠️ **A CRON JOB THAT HAS STOPPED IS INDISTINGUISHABLE FROM A CLEAN TREE** —
both show nothing to do, and cron mail goes nowhere. So every `--apply`
stamps `~/.attic/last_sweep`, and the consumer's `push.sh` warns when that
stamp goes stale or the entry disappears, putting the liveness of the
cleanup in front of a human during work they already do.

Installing it produced its own instance of that bug: `eval echo
"...>> $HOME/.attic/cron.log"` *performed* the redirect instead of
expanding it and installed a marker comment with an **empty job line** —
cron running, nothing scheduled, reading as installed. Hence the verified
non-empty job line in the installer.

A second instance, same shape: the sweep's walk only visits run dirs, so
when 610 digital items joined the selection it reported them chosen and
moved none — which also reads as a clean tree. Step 1b now moves anything
selected the walk does not reach.

## 7. What this does NOT fix

⚠️ **A SWEEP CANNOT FIX A STALE FILE BEING READ AS A FRESH RESULT.** That
happens *during* a run — a step that silently does not write, and a later
step reading the previous run's output believing it is this one's. There is
one on record: a stream step that never streamed, so the DRC after it
measured the previous build's GDS and reported it as current. A cleanup at
03:00 is always too late.

The guard for that belongs at write time — delete the output, assert it is
gone, produce it, assert it is present and newer than its inputs — and the
durable version is the producer recording what it made (`prepare()` writing
a `.run.json`: cell, tool, purpose, input digest, start time, completion on
exit). Then the sweep asks instead of infers: an incomplete run is stale
immediately rather than in 14 days, and a superseded one the moment its
successor finishes.

## 8. Status

The operating point is argued and instrumented; it is **not validated**.
No labels exist yet. `housekeeping/stale_labels.sample.json` is stratified
across the decision/confidence strata — set each `label` to `KEEP` or
`SWEEP`, then `stale_classify.py <features> --labels <file>` prints the
matrix for the rules and the age-only baseline side by side. Regenerate the
sample with `--sample` first: the committed one predates the three classes
added after the run-dir work.
