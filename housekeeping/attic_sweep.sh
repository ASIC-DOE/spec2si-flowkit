#!/bin/bash
# Continuous cleanup for the BNL cluster home. Moves nothing by default.
#
#   attic_sweep.sh                 # DRY RUN -- print what would move
#   attic_sweep.sh --apply         # actually move it
#   attic_sweep.sh --days 30       # change what counts as stale (default 14)
#
# ── WHAT THIS FIXES, AND WHAT IT DOES NOT ────────────────────────────────
# Measured 2026-08-26: 468 run dirs across five project trees (325 untouched
# for >14 days), 334 loose files in $HOME, and NO crontab -- nothing had ever
# cleaned up anything. Disk is not the constraint (37 TB free of 73 TB), so
# the goal is a findable working set, not reclaimed bytes.
#
# ⚠️ A SWEEP CANNOT FIX A STALE FILE BEING READ AS A FRESH RESULT. That
# failure happens DURING a run -- a step that silently does not write, and a
# later step that reads last week's output believing it is this run's (the
# repo has one: a stream step that never streamed, so the DRC after it
# measured the PREVIOUS build's GDS and reported it as current). A cleanup
# that runs at 03:00 is always too late for that. The guard for it belongs at
# write time -- delete the output, assert it is gone, produce it, assert it
# is present and newer than its inputs -- and is NOT this script's job.
# This script only keeps the tree navigable.
#
# ── SAFETY ───────────────────────────────────────────────────────────────
# * Nothing is deleted for 90 days. Stale dirs MOVE to ~/.attic/<date>/ and
#   are removed only when that dated folder is itself 90 days old.
# * `.keep` in a run dir exempts it forever. That is the only opt-out, by
#   design: an explicit marker beats a rule nobody can remember.
# * Dotfiles in $HOME are NEVER touched -- ~/.ssh, ~/.cadence, ~/.k5login and
#   the tool configs live there and moving one breaks the account.
# * Staleness is the newest file ANYWHERE inside the dir, not the dir's own
#   mtime. A directory's mtime does not change when a run writes deep inside
#   it, so the naive test calls an active campaign stale.
set -u

DAYS=14
EXPIRE=90
APPLY=0
ATTIC="$HOME/.attic"
STAMP=$(date +%Y-%m-%d)
LOG="$ATTIC/sweep.log"

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --days)  DAYS="$2"; shift ;;
        --expire) EXPIRE="$2"; shift ;;
        --attic) ATTIC="$2"; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# refuse to run anywhere unexpected: this script moves directories
case "${HOME:-}" in
    /u/home/*|/home/*) : ;;
    *) echo "attic_sweep: HOME=${HOME:-unset} is not a cluster home -- refusing" >&2
       exit 2 ;;
esac

moved=0
skipped_keep=0
skipped_fresh=0
say() { if [ "$APPLY" = 1 ]; then echo "$*" | tee -a "$LOG"; else echo "$*"; fi; }

[ "$APPLY" = 1 ] && mkdir -p "$ATTIC/$STAMP"

say "=== attic_sweep $(date '+%F %T')  mode=$([ $APPLY = 1 ] && echo APPLY || echo DRY-RUN)  stale=>${DAYS}d  attic-expiry=${EXPIRE}d"

# ── 1. run directories ───────────────────────────────────────────────────
#
# ⚠️ **THE DECISION IS THE CLASSIFIER'S, NOT THIS SCRIPT'S.** Age alone was
# never a policy, it was an unmeasured one-feature classifier: on this tree
# it sweeps 323 dirs, of which 88 hold signoff artifacts, while calling
# `drc_v1`..`drc_v15` fresh because they are recent -- and `drc_v2_ant` was
# obsolete at 2.3 days old, superseded by a successor a day younger.
# `stale_classify.py` decides; this script only moves what it is told, so
# there is exactly ONE definition of stale in the system and it is the one
# that can be evaluated against labels.
#
# Falls back to the age test only if the classifier is not installed, and
# SAYS SO rather than quietly reverting to the worse rule.
SWEEP_LIST=""
if [ -x "$HOME/bin/run_features.py" ] || [ -f "$HOME/bin/run_features.py" ]; then
    if python3 "$HOME/bin/run_features.py" > "$ATTIC/features.json" 2>/dev/null \
       && python3 "$HOME/bin/stale_classify.py" "$ATTIC/features.json" \
            --age-days "$DAYS" --sweep-list > "$ATTIC/sweep_list.txt" 2>/dev/null; then
        SWEEP_LIST="$ATTIC/sweep_list.txt"
        say "  classifier: $(wc -l < "$SWEEP_LIST") dirs selected (features: $ATTIC/features.json)"
    fi
fi
if [ -z "$SWEEP_LIST" ]; then
    say "  WARNING: stale_classify.py unavailable -- falling back to the AGE-ONLY"
    say "           rule, which sweeps finished results. Reinstall from"
    say "           deployment/bnl/ and re-run."
fi

for w in "$HOME"/Documents/*/analog/work "$HOME"/Documents/*/work; do
    [ -d "$w" ] || continue
    tree=$(echo "$w" | sed "s|$HOME/Documents/||; s|/|_|g")
    for d in "$w"/*/; do
        [ -d "$d" ] || continue
        name=$(basename "$d")
        if [ -e "$d/.keep" ]; then
            skipped_keep=$((skipped_keep + 1))
            continue
        fi
        if [ -n "$SWEEP_LIST" ]; then
            # the classifier already applied every rule, .keep included
            if ! grep -qxF "${d%/}" "$SWEEP_LIST"; then
                skipped_fresh=$((skipped_fresh + 1))
                continue
            fi
        else
            # fallback: any file inside written within DAYS? -quit stops at
            # the first hit, so this stays cheap over hundreds of directories
            if [ -n "$(find "$d" -newermt "-${DAYS} days" -print -quit 2>/dev/null)" ]; then
                skipped_fresh=$((skipped_fresh + 1))
                continue
            fi
        fi
        dest="$ATTIC/$STAMP/$tree"
        if [ "$APPLY" = 1 ]; then
            mkdir -p "$dest"
            if mv "$d" "$dest/$name" 2>/dev/null; then
                say "  moved  $w/$name -> ${dest#$HOME/}/$name"
                moved=$((moved + 1))
            else
                say "  FAILED $w/$name (in use? permissions?)"
            fi
        else
            say "  would move  $w/$name"
            moved=$((moved + 1))
        fi
    done
done

# ── 1b. everything else the classifier selected ──────────────────────────
#
# ⚠️ **THE LOOP ABOVE ONLY VISITS `*/analog/work/*/`, SO IT CANNOT ACT ON A
# SELECTION OUTSIDE IT.** When digital-flow scratch joined the feature set
# the classifier began selecting 610 items the sweep would never have
# reached -- it would have reported them as chosen and moved none of them,
# which reads exactly like a clean tree. Anything on the list that the walk
# did not already handle is moved here, by its own path.
#
# These entries are FILES as often as directories (`innovus.log3`), and
# every one of them is scratch by construction: `run_features.py` never
# enumerates `output/`, `reports/`, `*.db` or `*.odb`, so they cannot appear
# on this list. Verified after the change: 0 of 808 selections matched a
# protected class.
dig=0
if [ -n "$SWEEP_LIST" ]; then
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        case "$p" in "$HOME"/Documents/*/analog/work/*|"$HOME"/Documents/*/work/*)
            continue ;;              # already handled by the walk above
        esac
        [ -e "$p" ] || continue
        case "$p" in
            "$HOME"/Documents/*)
                rel=${p#$HOME/Documents/}
                sub=$(dirname "$rel" | sed 's|/|_|g') ;;
            "$HOME"/*)
                sub=HOME ;;               # loose $HOME files land readably
            *)  sub=other ;;
        esac
        dest="$ATTIC/$STAMP/$sub"
        if [ "$APPLY" = 1 ]; then
            mkdir -p "$dest"
            if mv "$p" "$dest/$(basename "$p")" 2>/dev/null; then
                dig=$((dig + 1))
            else
                say "  FAILED $p (a live tool may hold an .nfs handle in it)"
            fi
        else
            dig=$((dig + 1))
        fi
    done < "$SWEEP_LIST"
fi
say "  $([ $APPLY = 1 ] && echo moved || echo 'would move') $dig item(s) outside the run-dir walk (digital scratch, loose files, scratch scripts)"

# ── 2. loose files in $HOME ──────────────────────────────────────────────
#
# ⚠️ THIS USED TO BE ITS OWN `find ! -newermt`, which was a SECOND
# DEFINITION OF STALE sitting beside the classifier -- precisely what
# splitting features from decision was meant to stop. It also could not see
# what the classifier can: that a loose `.sh` may be GIT-TRACKED SOURCE
# (photonic_wirebond has 13) or named by a login file, neither of which age
# distinguishes from a scratch script. Those rows are classified now, and
# step 1b above moves whatever is selected, $HOME included.
loose=0
if [ -n "$SWEEP_LIST" ]; then
    loose=$(grep -c "^$HOME/[^/]*$" "$SWEEP_LIST" 2>/dev/null || echo 0)
else
    # fallback only, and it is the blunt rule: no classifier, no guards
    while IFS= read -r f; do
        [ -f "$f" ] || continue
        base=$(basename "$f")
        case "$base" in .*) continue ;; esac      # dotfiles stay, always
        dest="$ATTIC/$STAMP/HOME"
        if [ "$APPLY" = 1 ]; then
            mkdir -p "$dest"
            mv "$f" "$dest/$base" 2>/dev/null && loose=$((loose + 1))
        else
            loose=$((loose + 1))
        fi
    done < <(find "$HOME" -maxdepth 1 -type f ! -newermt "-${DAYS} days" 2>/dev/null)
fi
say "  $([ $APPLY = 1 ] && echo counted || echo 'would move') $loose loose \$HOME file(s) (dotfiles never)"

# ── 3. expire the attic ──────────────────────────────────────────────────
expired=0
for old in "$ATTIC"/20*/; do
    [ -d "$old" ] || continue
    if [ -z "$(find "$old" -maxdepth 0 -newermt "-${EXPIRE} days" 2>/dev/null)" ]; then
        if [ "$APPLY" = 1 ]; then
            rm -rf "$old" && say "  expired $old (older than ${EXPIRE}d)"
        else
            say "  would expire $old (older than ${EXPIRE}d)"
        fi
        expired=$((expired + 1))
    fi
done

say "=== run dirs: $moved swept, $skipped_keep kept (.keep), $skipped_fresh active; loose files: $loose; attic folders expired: $expired"

# ── 4. heartbeat ─────────────────────────────────────────────────────────
# ⚠️ **A CRON JOB THAT STOPS RUNNING LOOKS EXACTLY LIKE A CLEAN TREE.** Both
# show no output and nothing to do, and cron mail on this box goes nowhere.
# An enforcement nobody can see the health of is the same failure this repo
# keeps paying for -- a check that goes quiet and reads as a pass. So the
# sweep stamps every successful APPLY run, and `push.sh` prints a warning
# when that stamp goes stale, which puts the liveness of the cleanup in
# front of a human during work they are already doing.
if [ "$APPLY" = 1 ]; then
    date +%s > "$ATTIC/last_sweep"
    printf '%s swept=%s kept=%s active=%s loose=%s expired=%s\n' \
        "$(date '+%F %T')" "$moved" "$skipped_keep" "$skipped_fresh" \
        "$loose" "$expired" >> "$ATTIC/last_sweep.txt"
fi

if [ "$APPLY" != 1 ]; then
    echo
    echo "DRY RUN -- nothing was moved. Re-run with --apply to act."
    echo "To protect a run dir forever:  touch <dir>/.keep"
fi
