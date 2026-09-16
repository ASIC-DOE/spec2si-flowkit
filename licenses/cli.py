#!/usr/bin/env python3
"""CLI for the FlexLM license checker (spec2si-flowkit/licenses).

Defaults describe the BNL inst.bnl.gov cluster (see lm.DEFAULT_SETTINGS)
but every flag below overrides one of those defaults, so this points at any
cluster whose license servers speak FlexLM and are reachable over ssh --
persist a non-default cluster with `config --save` instead of repeating
flags every time.

  python3 -m licenses.cli list
  python3 -m licenses.cli list --json
  python3 -m licenses.cli who Xcelium_SC_DMS_Option
  python3 -m licenses.cli who xcelium_dms          # alias, see lm.ALIASES

  # point at a different cluster, once, persisted to ~/.licenses_checker.json:
  python3 -m licenses.cli config --save \\
      --host mycluster --lic-server lic.example.com \\
      --lmstat /opt/cadence/bin/lmstat \\
      --server 27000:Cadence --server 1717:Mentor
"""
import argparse
import json
import sys

try:
    from . import lm
except ImportError:  # allow `python3 cli.py` as well as `-m licenses.cli`
    import lm


def _parse_server_args(specs):
    """['27000:Cadence', '1717:Mentor'] -> [[27000, 'Cadence'], [1717, 'Mentor']]"""
    servers = []
    for spec in specs:
        port_s, _, label = spec.partition(":")
        try:
            port = int(port_s)
        except ValueError:
            raise SystemExit("bad --server %r (want PORT:LABEL)" % spec)
        servers.append([port, label or ("port %d" % port)])
    return servers


def _settings_from_args(ns):
    overrides = {
        "host": ns.host,
        "lic_server_host": ns.lic_server,
        "lmstat": ns.lmstat,
        "lmstat_glob": ns.lmstat_glob,
    }
    if ns.server:
        overrides["servers"] = _parse_server_args(ns.server)
    return lm.load_settings(config_path=ns.config, overrides=overrides)


def _print_list(snapshot):
    if not snapshot["fetched_ok"]:
        print("could not reach %s: %s" % (snapshot["host"], snapshot["error"]))
        return 1
    for family, info in snapshot["families"].items():
        header = "%s (port %d)" % (family, info["port"])
        if not info["reachable"]:
            print("%s -- UNREACHABLE" % header)
            continue
        print(header)
        feats = info.get("features", {})
        if not feats:
            print("  (no features reported)")
            continue
        for name, f in sorted(feats.items()):
            print("  %-42s %3d/%-3d in use  (%d free)" %
                  (name, f["in_use"], f["issued"], f["free"]))
    return 0


def _print_who(snapshot, name):
    if not snapshot["fetched_ok"]:
        print("could not reach %s: %s" % (snapshot["host"], snapshot["error"]))
        return 1
    family, feat = lm.resolve_feature(snapshot, name)
    if feat is None:
        print("no such feature: %s (run `list` for exact names)" % name)
        return 1
    print("%s -- %s: %d/%d in use" %
          (family, lm.ALIASES.get(name, name), feat["in_use"], feat["issued"]))
    if not feat["users"]:
        print("  (no active sessions)")
        return 0
    for u in feat["users"]:
        held = lm.format_duration(u["start"])
        print("  %-14s %-30s start %-16s  held %s" %
              (u["user"], u["host"], u["start"], held))
    return 0


def _cmd_config(ns):
    settings = _settings_from_args(ns)
    if ns.save:
        path = lm.save_settings(settings, config_path=ns.config)
        print("saved to %s" % path)
    print(json.dumps(settings, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="FlexLM license checker")
    p.add_argument("--config", default=None,
                   help="JSON settings file (default: ~/.licenses_checker.json)")
    p.add_argument("--host", default=None,
                   help="ssh target that can reach the license servers "
                        "(default from config, else asic6)")
    p.add_argument("--lic-server", default=None,
                   help="license-server hostname (default from config, "
                        "else iolicense2.inst.bnl.gov)")
    p.add_argument("--lmstat", default=None,
                   help="absolute lmstat/lmutil path on the remote host")
    p.add_argument("--lmstat-glob", default=None,
                   help="glob to search for lmstat if --lmstat isn't there")
    p.add_argument("--server", action="append", metavar="PORT:LABEL",
                   help="define a license-server port + family label; "
                        "repeatable. Any use REPLACES the whole server "
                        "list (BNL's or the config file's) with just what "
                        "you pass here.")
    p.add_argument("--timeout", type=float, default=lm.SSH_TIMEOUT)
    sub = p.add_subparsers(dest="cmd")

    lp = sub.add_parser("list", help="seat counts grouped by tool family")
    lp.add_argument("--json", action="store_true")

    wp = sub.add_parser("who", help="who holds a feature's seats, and for how long")
    wp.add_argument("feature")
    wp.add_argument("--json", action="store_true")

    cp = sub.add_parser("config", help="show (and optionally persist) the "
                        "effective cluster settings")
    cp.add_argument("--save", action="store_true",
                    help="write the effective settings to --config / "
                         "~/.licenses_checker.json")
    return p


def main(argv=None):
    ns = build_parser().parse_args(argv)
    cmd = ns.cmd or "list"

    if cmd == "config":
        return _cmd_config(ns)

    settings = _settings_from_args(ns)
    snapshot = lm.collect(settings=settings, timeout=ns.timeout)

    as_json = getattr(ns, "json", False)
    if cmd == "list":
        if as_json:
            print(json.dumps(snapshot))
            return 0 if snapshot["fetched_ok"] else 1
        return _print_list(snapshot)

    # who
    if as_json:
        _, feat = lm.resolve_feature(snapshot, ns.feature)
        print(json.dumps(feat))
        return 0 if feat is not None else 1
    return _print_who(snapshot, ns.feature)


if __name__ == "__main__":
    sys.exit(main())
