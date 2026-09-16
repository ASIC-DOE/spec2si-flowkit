"""FlexLM license checker -- query a cluster's license servers, grouped by
tool family, with per-feature holder/duration detail on request.

Ships with BNL inst.bnl.gov defaults (see lm.DEFAULT_SETTINGS) but every
field is overridable via CLI flags, a JSON config file, or the GUI's
Cluster settings dialog -- see lm.load_settings().
"""
