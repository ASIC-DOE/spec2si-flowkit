#!/bin/sh
# report.sh -- the cluster-side reader for the job-status transport.
#
# This is the ONE script the laptop runs to read cluster state. It is
# shipped once (content-hashed) by remote.py and thereafter invoked as
#   ssh <host> /bin/sh $HOME/.asicjobs/bin/report.sh <subcommand> [args]
# (in practice remote.py pipes a one-line launcher on stdin; same effect).
#
# CONTRACT (must never break -- remote.py depends on it):
#   * Emit EXACTLY ONE line of JSON on STDOUT, the {"schema":1,...} envelope.
#     Nothing else may reach stdout -- all diagnostics go to STDERR.
#   * Always exit 0 when a well-formed envelope was printed, even for
#     "not found" / error answers: the envelope itself carries the verdict.
#     A non-zero exit or non-envelope stdout is what tells the caller the
#     TRANSPORT failed, which is a different thing from "the job is gone".
#   * Pure POSIX sh. System /usr/bin/python3 is 3.6.8 and the tool env is
#     unavailable here, so NO python, NO jq, NO bashisms.
#   * Foundry NDA: emit ids, states, counts, epochs -- NEVER paths under a
#     PDK/model root, netlists, or log lines. (This reader only ever reads
#     $JOBS metadata, which is structured and path-free by construction.)
#
# Subcommands:
#   probe            (default) cluster reachability + $JOBS health
#   list             one-line summary of every job (Phase 1)
#   status <jobid>   one job's published status.json (schema>=1) -- Phase 1
#   events [n]       last n terminal events from events.jsonl     -- Phase 3
#   why <jobid>      diagnosis bundle: status + result + live ps  -- Phase 3
#   request <key>    the job that owns a caller request key, or "absent"
#   signatures <jobid>  counts of fixed failure signatures in the job's logs
set -u

SCHEMA=1
JOBS="${ASICJOBS_DIR:-$HOME/.asicjobs}"

# --- tiny JSON helpers (value-only; keys are literals in the printf) ------
# We only ever emit numbers, booleans, and strings drawn from a known-safe
# charset (hostnames, jobids, fixed states), so escaping is a backslash +
# double-quote pass -- enough for this closed vocabulary, and it keeps us
# free of jq.
jstr() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

fail_envelope() {
	# $1 = machine reason (safe token). Still schema-valid, still exit 0:
	# the caller gets a KNOWN answer that says "the reader could not answer",
	# which it maps to UNKNOWN -- never to a false "job absent".
	printf '{"schema":%s,"kind":"error","reason":"%s"}\n' "$SCHEMA" "$(jstr "$1")"
	exit 0
}

now() { date +%s 2>/dev/null || echo 0; }

host_short() { hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown; }

load1() {
	# first field of loadavg; '0' if unreadable. No paths leak.
	if [ -r /proc/loadavg ]; then
		read -r l _ </proc/loadavg 2>/dev/null && printf '%s' "$l" && return
	fi
	echo 0
}

ncpu() {
	# online CPU threads; '0' if unknowable. load1 ALONE cannot rank hosts
	# -- load 4 is idle on a 32-thread box and busy on a 20-thread one --
	# so capacity-based host selection needs this alongside it.
	nproc 2>/dev/null && return
	if [ -r /proc/cpuinfo ]; then
		grep -c '^processor' /proc/cpuinfo 2>/dev/null && return
	fi
	echo 0
}

cmd_probe() {
	_host=$(host_short)
	_epoch=$(now)
	_load=$(load1)
	_ncpu=$(ncpu)
	# $JOBS lives on the shared NFS home. Touching it triggers the autofs
	# mount; report whether it is present and how many job dirs exist. A
	# first-access ENOENT here is the automount race remote.py retries.
	if [ -d "$JOBS" ]; then
		_ok=true
		# count immediate subdirectories of $JOBS/ that look like job dirs
		# (contain a meta.json). Pure sh, no find -maxdepth portability risk.
		_n=0
		for d in "$JOBS"/*/; do
			[ -e "$d" ] || continue          # no-match glob stays literal
			[ -f "${d}meta.json" ] && _n=$((_n + 1))
		done
	else
		_ok=false
		_n=0
	fi
	printf '{"schema":%s,"kind":"probe","host":"%s","epoch":%s,"load1":%s,"ncpu":%s,"jobs_dir_ok":%s,"njobs":%s}\n' \
		"$SCHEMA" "$(jstr "$_host")" "$_epoch" "$_load" "$_ncpu" "$_ok" "$_n"
}

# pull one "key":<value> out of a job's json line. Works for our own
# controlled format (string OR bare number); returns empty if absent.
_field() {
	# $1 file, $2 key
	sed -n 's/.*"'"$2"'":"\{0,1\}\([^",}]*\)"\{0,1\}.*/\1/p' "$1" 2>/dev/null \
		| head -n1
}

cmd_list() {
	# include the SERVER clock so the observer can age heartbeats without
	# laptop/cluster clock skew.
	printf '{"schema":%s,"kind":"list","now":%s,"jobs":[' "$SCHEMA" "$(now)"
	_first=1
	if [ -d "$JOBS" ]; then
		for d in "$JOBS"/*/; do
			[ -e "$d" ] || continue
			[ -f "${d}meta.json" ] || continue
			_id=$(basename "$d")
			# result.json is authoritative once a job is terminal; else the
			# live status.json; else just meta (state=starting).
			if [ -f "${d}result.json" ]; then
				_src="${d}result.json"; _state=$(_field "$_src" state)
			elif [ -f "${d}status.json" ]; then
				_src="${d}status.json"; _state=$(_field "$_src" state)
			else
				_src="${d}meta.json"; _state=starting
			fi
			[ -n "$_state" ] || _state=unknown
			_flow=$(_field "${d}meta.json" flow)
			_target=$(_field "${d}meta.json" target)
			_started=$(_field "${d}meta.json" started)
			# $JOBS is on the shared NFS home, so EVERY host sees EVERY
			# job -- without this field a multi-host run is unreadable
			# (the same table answers identically from any host).
			_jhost=$(_field "${d}meta.json" host)
			[ -n "$_jhost" ] || _jhost=""
			_hb=$(_field "${d}status.json" heartbeat)
			_el=$(_field "$_src" elapsed_s)
			_frac=$(_field "${d}status.json" frac)
			_eta=$(_field "${d}status.json" eta_s)
			_lfree=$(_field "${d}status.json" free)
			# a progress record counting something OTHER than the
			# caller's unit (e.g. a calibrated deck's cal phase) has
			# no frac by construction -- carry the phase so the table
			# can say "cal" instead of an indistinguishable "-".
			_phase=$(_field "${d}status.json" phase)
			_rate=$(_field "${d}status.json" rate_per_s)
			[ -n "$_started" ] || _started=0
			[ -n "$_hb" ] || _hb=0
			[ -n "$_el" ] || _el=0
			[ -n "$_frac" ] || _frac=null
			[ -n "$_eta" ] || _eta=null
			[ -n "$_lfree" ] || _lfree=null
			[ -n "$_rate" ] || _rate=null
			[ -n "$_phase" ] || _phase=""
			[ "$_first" = 1 ] || printf ','
			printf '{"jobid":"%s","flow":"%s","target":"%s","host":"%s","state":"%s","started":%s,"heartbeat":%s,"elapsed_s":%s,"frac":%s,"phase":"%s","rate_per_s":%s,"eta_s":%s,"lic_free":%s}' \
				"$(jstr "$_id")" "$(jstr "$_flow")" "$(jstr "$_target")" \
				"$(jstr "$_jhost")" \
				"$(jstr "$_state")" "$_started" "$_hb" "$_el" "$_frac" \
				"$(jstr "$_phase")" "$_rate" "$_eta" \
				"$_lfree"
			_first=0
		done
	fi
	printf ']}\n'
}

cmd_status() {
	_id="${1:-}"
	[ -n "$_id" ] || fail_envelope "missing_jobid"
	# jobids are minted from a closed charset; reject anything else rather
	# than let a crafted arg walk the filesystem.
	case "$_id" in
		*[!A-Za-z0-9._-]*) fail_envelope "bad_jobid" ;;
	esac
	_sf="$JOBS/$_id/status.json"
	if [ ! -d "$JOBS/$_id" ]; then
		# Distinct from a transport miss: we reached $JOBS and the id is not
		# there. Envelope says NOTFOUND; caller decides (Phase 1 wires this).
		printf '{"schema":%s,"kind":"status","jobid":"%s","state":"NOTFOUND"}\n' \
			"$SCHEMA" "$(jstr "$_id")"
		return
	fi
	if [ -f "$_sf" ]; then
		# Phase 1 writes a schema>=1 envelope here already; pass it through
		# verbatim (it is the job's own published line). Until then the file
		# does not exist and we fall through.
		cat "$_sf"
		return
	fi
	printf '{"schema":%s,"kind":"status","jobid":"%s","state":"UNIMPLEMENTED"}\n' \
		"$SCHEMA" "$(jstr "$_id")"
}

cmd_events() {
	_n="${1:-50}"
	case "$_n" in *[!0-9]*|'') _n=50 ;; esac
	_ef="$JOBS/events.jsonl"
	# The ONE poll that answers "did anything finish?" across all jobs and
	# all hosts (plan L3). Each line is already a JSON object appended by a
	# job on exit; return the last _n of them, comma-joined, plus the server
	# clock so the client can age them without skew. `now` also lets the
	# client anchor a cursor even when the file is empty.
	_body=""
	if [ -f "$_ef" ]; then
		# grep keeps only well-formed object lines; paste joins with commas.
		_body=$(tail -n "$_n" "$_ef" 2>/dev/null | grep '^{' | paste -sd, - \
			2>/dev/null)
	fi
	printf '{"schema":%s,"kind":"events","now":%s,"events":[%s]}\n' \
		"$SCHEMA" "$(now)" "$_body"
}

cmd_verify() {
	# Re-check the jobid-stamped artifact hashes (Class-D). Emits COUNTS
	# only -- never the paths (NDA). verdict OK iff every stamped artifact
	# still matches the hash the job recorded; STALE if any changed/vanished
	# (e.g. a later run co-wrote the file), UNSTAMPED if the job hashed
	# nothing.
	_id="${1:-}"
	[ -n "$_id" ] || fail_envelope "missing_jobid"
	case "$_id" in *[!A-Za-z0-9._-]*) fail_envelope "bad_jobid" ;; esac
	_d="$JOBS/$_id"
	[ -d "$_d" ] || {
		printf '{"schema":%s,"kind":"verify","jobid":"%s","verdict":"NOTFOUND"}\n' \
			"$SCHEMA" "$(jstr "$_id")"; return; }
	_mf="$_d/artifacts.sha256"
	if [ ! -s "$_mf" ]; then
		printf '{"schema":%s,"kind":"verify","jobid":"%s","checked":0,"verdict":"UNSTAMPED"}\n' \
			"$SCHEMA" "$(jstr "$_id")"; return
	fi
	_total=$(grep -c '^' "$_mf" 2>/dev/null); [ -n "$_total" ] || _total=0
	# sha256sum -c prints "<path>: OK" / "<path>: FAILED" (and FAILED open).
	_res=$(sha256sum -c "$_mf" 2>/dev/null)
	_ok=$(printf '%s\n' "$_res" | grep -c ': OK$'); [ -n "$_ok" ] || _ok=0
	_bad=$((_total - _ok))
	if [ "$_bad" -le 0 ]; then _v=OK; else _v=STALE; fi
	printf '{"schema":%s,"kind":"verify","jobid":"%s","checked":%s,"ok":%s,"stale":%s,"verdict":"%s"}\n' \
		"$SCHEMA" "$(jstr "$_id")" "$_total" "$_ok" "$_bad" "$_v"
}

cmd_why() {
	_id="${1:-}"
	[ -n "$_id" ] || fail_envelope "missing_jobid"
	case "$_id" in *[!A-Za-z0-9._-]*) fail_envelope "bad_jobid" ;; esac
	_d="$JOBS/$_id"
	if [ ! -d "$_d" ]; then
		printf '{"schema":%s,"kind":"why","jobid":"%s","state":"NOTFOUND"}\n' \
			"$SCHEMA" "$(jstr "$_id")"
		return
	fi
	# embed the job's own JSON lines verbatim (they are already envelopes).
	_status=null
	[ -f "$_d/status.json" ] && _status=$(cat "$_d/status.json" 2>/dev/null)
	case "$_status" in \{*) ;; *) _status=null ;; esac
	_result=null
	[ -f "$_d/result.json" ] && _result=$(cat "$_d/result.json" 2>/dev/null)
	case "$_result" in \{*) ;; *) _result=null ;; esac
	# live ps state for the pid (running jobs): etimes = true elapsed
	# seconds, stat = R/S/D/Z... ('D' is an NFS/IO wait, not a hang). This
	# is the authoritative cross-check the plan prescribes over guessing.
	_ps=null
	_pid=$(_field "$_d/status.json" pid)
	if [ -n "$_pid" ] && [ "$_pid" -gt 0 ] 2>/dev/null; then
		_st=$(ps -o stat= -p "$_pid" 2>/dev/null | tr -d ' \n')
		_et=$(ps -o etimes= -p "$_pid" 2>/dev/null | tr -d ' \n')
		if [ -n "$_st" ]; then
			[ -n "$_et" ] || _et=0
			_ps=$(printf '{"pid":%s,"stat":"%s","etimes":%s,"alive":true}' \
				"$_pid" "$(jstr "$_st")" "$_et")
		else
			_ps=$(printf '{"pid":%s,"alive":false}' "$_pid")
		fi
	fi
	printf '{"schema":%s,"kind":"why","jobid":"%s","status":%s,"result":%s,"ps":%s}\n' \
		"$SCHEMA" "$(jstr "$_id")" "$_status" "$_result" "$_ps"
}

# request <key>: which job, if any, owns a caller's request key (runjob
# --request). The claim is a symlink $JOBS/requests/<key> -> <jobid>, made
# atomically before anything launches. "absent" is said only when $JOBS was
# reached: an unreadable $JOBS is an error envelope, never an absence.
cmd_request() {
	_k="${1:-}"
	[ -n "$_k" ] || fail_envelope "missing_request_key"
	case "$_k" in
		*[!A-Za-z0-9._-]*|.*) fail_envelope "bad_request_key" ;;
	esac
	{ [ -d "$JOBS" ] && [ -r "$JOBS" ] && [ -x "$JOBS" ]; } || fail_envelope "jobs_dir_unreadable"
	_c="$JOBS/requests/$_k"
	if [ ! -L "$_c" ]; then
		[ -e "$_c" ] && fail_envelope "request_claim_not_a_link"
		printf '{"schema":%s,"kind":"request","key":"%s","state":"absent"}\n' \
			"$SCHEMA" "$(jstr "$_k")"
		return
	fi
	_id=$(readlink "$_c" 2>/dev/null) || fail_envelope "request_claim_unreadable"
	case "$_id" in
		''|*[!A-Za-z0-9._-]*) fail_envelope "bad_request_claim" ;;
	esac
	# the claimant writes meta.json before it detaches anything; without it
	# the claim was taken but no job was (yet, or ever) started.
	_started=false
	[ -f "$JOBS/$_id/meta.json" ] && _started=true
	printf '{"schema":%s,"kind":"request","key":"%s","state":"claimed","jobid":"%s","started":%s}\n' \
		"$SCHEMA" "$(jstr "$_k")" "$(jstr "$_id")" "$_started"
}

# signatures <jobid>: how many lines of the job's own logs match each of a
# FIXED set of failure classes (licence, crash, environment, traceback,
# timeout, memory, disk). Counts only -- no line ever leaves the cluster
# (NDA). Read: $JOBS/<jobid>/stdout.log and the *.log files under the job's
# recorded cwd (its workspace), to depth 4, under 64 MB each.
# SIG_LICENSE counts DENIALS only. Every tool announces a SUCCESSFUL checkout
# (Genus "checkout complete", Virtuoso "checked out successfully ... checkout
# time", Innovus echoing setLicenseCheck -checkout), so a bare checkout/FLEXlm
# word scored a passing Genus run 9 and made failure.py call an early failure
# a licence tool-error (2026-09-26). A checkout counts only with a failure word.
SIG_LICENSE='SPECTRE-209|[Ll]icen[cs]e.{0,60}(unavailable|denied|not available|exhausted|expired|could not be checked out|check ?out (failed|error))|([Ff]ail(ed|ure)?|[Cc]annot|[Cc]ould not|[Uu]nable) to (check ?out|obtain|acquire|get) .{0,30}[Ll]icen[cs]e|(FLEXnet|FLEXlm) ([Ll]icensing )?[Ee]rror|[Ll]icen[cs]e server .{0,40}(down|not responding)|[Cc]annot connect to (the )?[Ll]icen[cs]e server|Licensed number of users already reached|No such feature exists'
SIG_CRASH='Segmentation fault|core dumped|SIGSEGV|Bus error|[Ii]nternal [Ee]rror|INTERNAL ERROR'
SIG_ENV='command not found|toolchain not activated|cannot open shared object'
SIG_TRACEBACK='Traceback \(most recent call last\)'
SIG_TIMEOUT='wall-clock timeout|TimeoutExpired|[Tt]imed out after'
SIG_MEMORY='Out of memory|MemoryError|Cannot allocate memory|std::bad_alloc'
SIG_DISK='No space left on device|Disk quota exceeded'

_sigcount() {
	# $1 pattern; files: stdout.log + workspace logs. Sum of matching lines.
	{
		[ -f "$_log" ] && grep -h -c -E "$1" "$_log" 2>/dev/null
		[ -n "$_ws" ] && find "$_ws" -maxdepth 4 -type f -name '*.log' -size -65536k \
			-exec grep -h -c -E "$1" {} + 2>/dev/null
	} | awk '{ s += $1 } END { printf "%d", s + 0 }'
}

cmd_signatures() {
	_id="${1:-}"
	[ -n "$_id" ] || fail_envelope "missing_jobid"
	case "$_id" in
		*[!A-Za-z0-9._-]*) fail_envelope "bad_jobid" ;;
	esac
	[ -d "$JOBS/$_id" ] || fail_envelope "job_not_found"
	_log="$JOBS/$_id/stdout.log"
	_ws=$(sed -n 's/.*"cwd":"\([^"]*\)".*/\1/p' "$JOBS/$_id/meta.json" 2>/dev/null | head -n1)
	case "$_ws" in
		/*) [ -d "$_ws" ] || _ws="" ;;
		*) _ws="" ;;
	esac
	# the cwd of a job launched from $HOME is not a workspace: do not scan it.
	[ "$_ws" = "$HOME" ] && _ws=""
	_nf=0
	[ -f "$_log" ] && _nf=1
	if [ -n "$_ws" ]; then
		_nf=$((_nf + $(find "$_ws" -maxdepth 4 -type f -name '*.log' -size -65536k 2>/dev/null | wc -l)))
	fi
	printf '{"schema":%s,"kind":"signatures","jobid":"%s","files":%s,"license":%s,"crash":%s,"environment":%s,"traceback":%s,"timeout":%s,"memory":%s,"disk":%s}\n' \
		"$SCHEMA" "$(jstr "$_id")" "$_nf" "$(_sigcount "$SIG_LICENSE")" "$(_sigcount "$SIG_CRASH")" \
		"$(_sigcount "$SIG_ENV")" "$(_sigcount "$SIG_TRACEBACK")" "$(_sigcount "$SIG_TIMEOUT")" \
		"$(_sigcount "$SIG_MEMORY")" "$(_sigcount "$SIG_DISK")"
}

sub="${1:-probe}"
[ $# -gt 0 ] && shift
case "$sub" in
	probe)  cmd_probe ;;
	list)   cmd_list ;;
	status) cmd_status "$@" ;;
	events) cmd_events "$@" ;;
	why)    cmd_why "$@" ;;
	verify) cmd_verify "$@" ;;
	request) cmd_request "$@" ;;
	signatures) cmd_signatures "$@" ;;
	*)      fail_envelope "unknown_subcommand" ;;
esac
