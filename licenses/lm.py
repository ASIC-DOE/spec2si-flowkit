#!/usr/bin/env python3
"""lm.py -- FlexLM license snapshot core: query every configured license
server on a cluster, group by tool family, and expose per-feature seat
counts plus (on request) who holds them and for how long.

Pure stdlib, 3.6+. Talks to the cluster over ssh (WSL-wrapped on Windows,
plain ssh elsewhere) by PIPING a POSIX /bin/sh script to `ssh <host> /bin/sh
-s` -- never an interpolated command string. That is the one thing this
module borrows deliberately from the spec2si-tsmc65 jobs/remote.py Transport:
piping to `/bin/sh -s` sidesteps a tcsh login shell's history-expansion and
quoting hazards entirely (CLUSTER.md's "Ambiguous output redirect" class),
because the login shell never sees the script -- ssh hands it straight to a
non-interactive /bin/sh.

The remote script itself needs no Cadence/EDA environment activation: it
calls lmstat with an explicit `-c host:port` and an explicit (or
glob-discovered) binary path, so it works over a bare `ssh host /bin/sh -s`
with no `source pdk_setup.csh` / `asic-setup.csh` step (confirmed live
2026-08-31 -- sourcing the tcsh env is only needed to put lmstat on PATH and
set LM_LICENSE_FILE, both of which an explicit path + explicit server make
unnecessary).

Defaults describe the BNL inst.bnl.gov cluster, but nothing here is
BNL-specific: every field lives in `DEFAULT_SETTINGS` and can be overridden
by a JSON config file (`load_settings`), CLI flags (see cli.py), or the
GUI's Cluster settings dialog (see gui.py) -- point it at any cluster whose
license servers speak FlexLM and are reachable over ssh.

  python3 -m licenses.cli list
  python3 -m licenses.cli who Xcelium_SC_DMS_Option
"""
import datetime
import json
import os
import re
import subprocess
import time

# --- settings ---------------------------------------------------------

#: BNL inst.bnl.gov defaults. Every key here is overridable -- see
#: load_settings(). `servers` is [[port, family_label], ...] in display
#: order; ports/labels confirmed live 2026-08-31 by querying each port
#: directly (CLUSTER.md documents 7180/7183/7184/7186 already; 7182/7188/7190
#: confirmed here as MathWorks/Cliosoft/DVT respectively).
DEFAULT_SETTINGS = {
    "host": "asic6",
    "lic_server_host": "iolicense2.inst.bnl.gov",
    "lmstat": "/u/cad/cds/IC251/tools/bin/lmstat",
    "lmstat_glob": "/u/cad/cds/*/tools/bin/lmstat",
    "servers": [
        [7180, "Synopsys (HSPICE)"],
        [7182, "MathWorks (MATLAB/Simulink)"],
        [7183, "Cadence"],
        [7184, "Siemens (Calibre)"],
        [7186, "xgdsplot"],
        [7188, "Cliosoft (SOS)"],
        [7190, "DVT (Verilog/VHDL IDE)"],
    ],
}

#: convenience feature-name aliases -- best-effort; exact strings vary by
#: kit/version, so `list` always shows the raw names too.
ALIASES = {
    "spectre": "Virtuoso_Multi_mode_Simulation",
    "virtuoso": "Virtuoso_Multi_mode_Simulation",
    "xcelium": "Xcelium_Single_Core",
    "xcelium_dms": "Xcelium_SC_DMS_Option",
    "matlab": "MATLAB",
    "simulink": "SIMULINK",
}

SSH_TIMEOUT = 45.0  # wall clock; covers every configured server, paced 1s apart
CONNECT_TIMEOUT = 10


def _user_config_path():
    return os.environ.get("LICCHECK_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".licenses_checker.json")


def load_settings(config_path=None, overrides=None):
    """DEFAULT_SETTINGS, layered with a JSON config file (if present) and
    then explicit overrides (e.g. CLI flags). Never raises: a missing or
    malformed config file is silently ignored, same as "no file yet"."""
    settings = dict(DEFAULT_SETTINGS)
    settings["servers"] = [list(s) for s in DEFAULT_SETTINGS["servers"]]
    path = config_path or _user_config_path()
    if path and os.path.isfile(path):
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
            for k in DEFAULT_SETTINGS:
                if k in data:
                    settings[k] = data[k]
        except (OSError, ValueError):
            pass
    if overrides:
        for k, v in overrides.items():
            if v is not None and k in DEFAULT_SETTINGS:
                settings[k] = v
    return settings


def save_settings(settings, config_path=None):
    path = config_path or _user_config_path()
    with open(path, "w") as fh:
        json.dump({k: settings[k] for k in DEFAULT_SETTINGS}, fh, indent=2)
    return path


# --- remote script construction ----------------------------------------

#: closed charset for anything spliced into the remote sh script -- paths,
#: hostnames, globs. Not a security boundary against the cluster itself
#: (the caller already has an ssh account there); it is a guard against a
#: malformed config file producing a broken or surprising remote script.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._/*@:-]+$")


def _safe(s):
    return isinstance(s, str) and bool(s) and bool(_SAFE_TOKEN.match(s))


class SettingsError(ValueError):
    pass


def _validate(settings):
    for key in ("host", "lic_server_host", "lmstat", "lmstat_glob"):
        if not _safe(settings.get(key, "")):
            raise SettingsError("invalid %s: %r" % (key, settings.get(key)))
    servers = settings.get("servers") or []
    if not servers:
        raise SettingsError("no servers configured")
    for port, label in servers:
        if not (isinstance(port, int) and 0 < port < 65536):
            raise SettingsError("invalid port: %r" % (port,))


_REMOTE_TEMPLATE = """\
LMSTAT=%(lmstat)s
if [ ! -x "$LMSTAT" ]; then
  LMSTAT=""
  for c in %(glob)s; do
    [ -x "$c" ] || continue
    case "$("$c" -c %(probe_port)s@%(lichost)s -a 2>&1)" in
      *"Users of"*) LMSTAT="$c"; break ;;
    esac
  done
fi
if [ -z "$LMSTAT" ]; then
  if command -v lmstat >/dev/null 2>&1; then
    LMSTAT=lmstat
  else
    echo "##LMSTAT_NOT_FOUND"
    exit 0
  fi
fi
%(loop)s
echo "##END"
"""


def build_remote_script(settings):
    """The POSIX /bin/sh script run on `settings["host"]`. Resolves the
    lmstat binary once (explicit path, else a glob validated against the
    first configured server), then queries every configured port in turn,
    paced 1s apart -- lmstat/FlexLM rate-limits back-to-back queries from
    the same client (measured on the BNL cluster; see procscan-licence-
    attribution memory), and a naive concurrent sweep measures WORSE, not
    better, so this stays serial by design."""
    _validate(settings)
    servers = settings["servers"]
    loop_lines = []
    for port, _label in servers:
        loop_lines.append('echo "##SERVER %d"' % port)
        loop_lines.append(
            'timeout 10 "$LMSTAT" -c %d@%s -a 2>&1' %
            (port, settings["lic_server_host"]))
        loop_lines.append("sleep 1")
    return _REMOTE_TEMPLATE % {
        "lmstat": settings["lmstat"],
        "glob": settings["lmstat_glob"],
        "probe_port": servers[0][0],
        "lichost": settings["lic_server_host"],
        "loop": "\n".join(loop_lines),
    }


# --- transport -----------------------------------------------------------

def _mode():
    env = os.environ.get("LICCHECK_RSH")
    if env:
        return env
    return "wsl" if os.name == "nt" else "ssh"


def _ssh_argv(host, mode):
    common = ["-o", "BatchMode=yes",
              "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
              "-o", "StrictHostKeyChecking=accept-new"]
    if mode == "wsl":
        return ["wsl", "ssh"] + common + [host, "/bin/sh", "-s"]
    if mode == "winssh":
        return ["ssh.exe"] + common + [host, "/bin/sh", "-s"]
    return ["ssh"] + common + [host, "/bin/sh", "-s"]


def _normalize(text):
    """UTF-8 bytes, LF-only, exactly one trailing newline -- defuses the
    CRLF/BOM class before the script crosses the wire."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not data.endswith(b"\n"):
        data += b"\n"
    return data


def fetch_raw(settings, timeout=SSH_TIMEOUT):
    """Run the remote sweep; (ok, stdout_text, error). Never raises."""
    try:
        script = _normalize(build_remote_script(settings))
    except SettingsError as exc:
        return False, "", str(exc)
    argv = _ssh_argv(settings["host"], _mode())
    try:
        proc = subprocess.run(argv, input=script, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "", "ssh timed out after %.0fs" % timeout
    except FileNotFoundError as exc:
        return False, "", "transport binary missing: %s" % exc
    out = proc.stdout.decode("utf-8", "replace")
    if "##LMSTAT_NOT_FOUND" in out:
        return False, out, "lmstat not found on %s (check --lmstat / --lmstat-glob)" % settings["host"]
    if proc.returncode != 0 or "##END" not in out:
        err = proc.stderr.decode("utf-8", "replace")
        return False, out, err.strip() or "ssh rc=%s" % proc.returncode
    return True, out, ""


# --- parsing ---------------------------------------------------------------

_USERS_OF = re.compile(
    r"^Users of (?P<feature>\S+?):\s*\(Total of (?P<issued>\d+) licenses? "
    r"issued;\s*Total of (?P<inuse>\d+) licenses? in use\)")
#: one in-use line; the trailing `(linger: N / M)` is optional (Cliosoft
#: emits it) and consumed here rather than left to corrupt the start field.
#: Some vendor daemons (Xcelium) insert a free-text checkout description
#: between `display` and `(version)` -- e.g. "fhong host :1 Xcelium Single
#: Core Engine (v26.000) (...)" -- so that gap is optional, lazy, non-paren
#: text rather than assumed absent.
_USAGE = re.compile(
    r"^\s+(?P<user>\S+)\s+(?P<host>\S+)\s+(?P<display>\S+)\s+"
    r"(?:[^()]*?\s+)?\((?P<version>[^)]*)\)\s+"
    r"\((?P<server>[^/\s]+)/(?P<port>\d+)\s+(?P<handle>\d+)\)"
    r",\s*start\s+(?P<start>\w{3}\s+\d+/\d+\s+\d+:\d+)"
    r"(?:\s*\(linger:\s*(?P<linger>[^)]*)\))?\s*$")
_UNREACHABLE = re.compile(
    r"Cannot connect to license server|license server DOWN|"
    r"Error getting status")


def parse_server_block(text):
    """{'reachable': bool, 'features': {name: {issued,in_use,free,users}}}"""
    if _UNREACHABLE.search(text or ""):
        return {"reachable": False, "features": {}}
    features = {}
    current = None
    for line in (text or "").splitlines():
        m = _USERS_OF.match(line)
        if m:
            issued = int(m.group("issued"))
            inuse = int(m.group("inuse"))
            current = m.group("feature")
            features[current] = {"issued": issued, "in_use": inuse,
                                 "free": issued - inuse, "users": []}
            continue
        m = _USAGE.match(line)
        if m and current is not None:
            features[current]["users"].append(m.groupdict())
    return {"reachable": True, "features": features}


def collect(settings=None, timeout=SSH_TIMEOUT):
    """{'fetched_ok', 'error', 'fetched_at', 'host', 'families': {label:
    {port, reachable, features}}}. Never raises."""
    settings = settings or load_settings()
    snapshot = {"fetched_ok": False, "error": "", "fetched_at": None,
                "host": settings.get("host"), "families": {}}
    ok, raw, err = fetch_raw(settings, timeout=timeout)
    snapshot["fetched_ok"] = ok
    snapshot["error"] = err
    if not ok:
        return snapshot
    snapshot["fetched_at"] = time.time()
    parts = re.split(r"^##SERVER (\d+)\s*$", raw, flags=re.M)
    it = iter(parts[1:])
    labels = {int(p): label for p, label in settings["servers"]}
    for port_str, body in zip(it, it):
        port = int(port_str)
        label = labels.get(port, "port %d" % port)
        parsed = parse_server_block(body)
        entry = {"port": port}
        entry.update(parsed)
        snapshot["families"][label] = entry
    return snapshot


# --- duration --------------------------------------------------------------

_START_RE = re.compile(r"\w{3}\s+(\d+)/(\d+)\s+(\d+):(\d+)")


def parse_start(start_str, now=None):
    """FlexLM's `start` field ('Sat 8/29 23:23') carries no year -- assume
    the current year, rolling back one if that would place it in the
    future (a session that started in December, queried in January)."""
    m = _START_RE.match(start_str or "")
    if not m:
        return None
    mon, day, hh, mm = (int(x) for x in m.groups())
    now = now or datetime.datetime.now()
    try:
        dt = datetime.datetime(now.year, mon, day, hh, mm)
    except ValueError:
        return None
    if dt > now + datetime.timedelta(hours=1):
        dt = dt.replace(year=now.year - 1)
    return dt


def format_duration(start_str, now=None):
    """Human 'held' string ('2d 3h', '45m') from a FlexLM start field, or
    '?' if it cannot be parsed."""
    dt = parse_start(start_str, now=now)
    if dt is None:
        return "?"
    now = now or datetime.datetime.now()
    secs = max(0, int((now - dt).total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %dm" % (hours, minutes)
    return "%dm" % minutes


def resolve_feature(snapshot, name):
    """(family, feature_dict) for an alias or exact feature name across
    every family in `snapshot`, or (None, None) if not found."""
    name = ALIASES.get(name, name)
    for family, info in snapshot.get("families", {}).items():
        feat = info.get("features", {}).get(name)
        if feat is not None:
            return family, feat
    return None, None
