"""Run harmless local tracker compatibility jobs (POSIX/WSL); never uses SSH.

Temporary work and evidence are removed on success. The JSON summary is stdout.
This is a runtime/package check, not a model/harness activation canary.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .remote import Transport
from .state import TaskStore
from .workflow import Workflow, ContractError


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(isolated_bundle=False):
    check(os.name == "posix", "run the smoke command under POSIX/WSL")
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="flowkit-compat-") as home:
        env = dict(os.environ, HOME=home)
        for name in ("ASICJOBS_DIR", "ASICJOBS_ID", "ASICJOBS_JOBDIR", "ASICJOBS"):
            env.pop(name, None)
        def runner(argv, input_bytes, timeout):
            process = subprocess.Popen(["/bin/sh", "-s"], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=home)
            try:
                out, err = process.communicate(input_bytes, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise TimeoutError("local smoke timeout")
            return process.returncode, out, err
        legacy = Path(home) / ".asicjobs" / "bin" / "preserve"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("active consumer helpers")
        factory = lambda host, **kw: Transport(host=host, runner=runner, **kw)
        profile = dict(schema=1, id="compat", version="1", repository="flowkit-compat",
                       work_root=home, workspace="unique-child", lifecycle="foreground",
                       host_policy=dict(allowed=["synthetic.invalid"], default="synthetic.invalid", allow_auto=False),
                       parameters={"mode": {"type": "string", "choices": ["attached", "campaign", "disabled", "missing"]}},
                       argv=[sys.executable, str(Path(__file__).parent / "fixtures" / "compat_payload.py"), {"parameter": "mode"}],
                       expected_artifacts=["proof.json", "report.json"], progress=None,
                       engineering_report=dict(parser="json-v1", path="report.json", design="synthetic", top="payload",
                                               checks=["lifecycle"], corners=["synthetic"]))
        profile["isolated_bundle"] = isolated_bundle
        store = TaskStore(os.path.join(home, "state"))
        source = dict(root=os.path.join(home, "source"), head="a" * 40, patch_sha256="b" * 64, untracked_sha256="c" * 64)
        workspaces = set()
        for mode in ("attached", "campaign", "disabled", "missing"):
            workflow = Workflow(profile, factory)
            started = store.start(workflow, mode, {"mode": mode}, source, "d" * 64)
            check(started["observation"] == "submitted", "fixture submission failed")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = store.observe(workflow, mode, collect=True)
                if result["observation"] in ("done", "failed", "killed"):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("fixture did not terminate; inspect local temp jobs")
            ref = result["reference"]
            workspace = Path(ref["workspace"])
            check(str(workspace) not in workspaces, "workspace was reused")
            workspaces.add(str(workspace))
            if mode in ("attached", "campaign"):
                check(result["engineering"] == "pass", "valid fixture did not pass collection")
                proof = json.loads((workspace / "proof.json").read_text())
                check(proof["attached"] and proof["job_id"] == ref["job_id"], "recorder did not attach")
                check(not proof["parent_result_exists_before_exit"], "duplicate lifecycle owner")
                progress = json.loads((Path(home) / ".asicjobs" / ref["job_id"] / "progress.json").read_text())
                check(progress["done"] == 2, "attached progress was not published")
                if mode == "campaign":
                    check((workspace / "child.txt").read_text() == "finished", "campaign did not wait")
            else:
                check(result["observation"] == "failed" and result["engineering"] != "pass", "missing recorder was accepted")
                check(not (workspace / "work-started").exists(), "work ran without required recorder")
            outcomes.append(dict(case=mode, observation=result["observation"], engineering=result["engineering"]))
        jobs = Path(home) / ".asicjobs"
        check(legacy.read_text() == "active consumer helpers", "legacy helper overwritten")
        if isolated_bundle:
            check(list(legacy.parent.iterdir()) == [legacy], "shared bin was modified")
            check(len(list((jobs / "bundles").iterdir())) == 1, "bundle was not isolated")
        check(len(list(jobs.glob("*/meta.json"))) == 4, "an engine created a duplicate job")
        events = [json.loads(line) for line in (jobs / "events.jsonl").read_text().splitlines()]
        check(len(events) == 4 and len({e["jobid"] for e in events}) == 4, "duplicate lifecycle events")
        profile["lifecycle"] = "detached"
        try:
            Workflow(profile, factory)
        except ContractError:
            outcomes.append(dict(case="nested-detach", observation="refused-before-launch"))
        else:
            raise RuntimeError("nested-detach profile accepted")
    return dict(schema=1, kind="compatibility-smoke", cases=outcomes,
                jobs=4, terminal_events=4, isolated_workspaces=4, harness_activation="not-tested")


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
