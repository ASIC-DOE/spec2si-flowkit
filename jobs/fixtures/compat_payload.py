"""Synthetic foreground payload; no EDA tools, private inputs or remote access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jobs.adapter import require_attached


def main():
    mode = sys.argv[1]
    if mode not in ("attached", "campaign", "disabled", "missing"):
        raise RuntimeError("unsupported fixture mode")
    directory = Path(os.environ["ASICJOBS_JOBDIR"])
    if mode == "missing":
        require_attached(None)
    if mode == "disabled":
        os.environ["ASICJOBS"] = "0"
    bindir = Path(os.environ.get("ASICJOBS_BINDIR", str(directory.parent / "bin")))
    spec = importlib.util.spec_from_file_location("jobrec", bindir / "jobrec.py")
    recorder_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recorder_module)
    recorder = require_attached(recorder_module.begin(flow="fixture", target="payload", total=2))
    recorder.progress(1, 2, "stages")
    # The foreground campaign waits for the actual work rather than detaching.
    if mode == "campaign":
        subprocess.run([sys.executable, "-c", "from pathlib import Path; Path('child.txt').write_text('finished')"], check=True)
    Path("work-started").write_text("synthetic work only")
    proof = dict(job_id=recorder.jobid, attached=recorder.attached,
                 parent_result_exists_before_exit=(directory / "result.json").exists())
    Path("proof.json").write_text(json.dumps(proof))
    recorder.finalize(0)
    if (directory / "result.json").exists():
        raise RuntimeError("attached engine incorrectly finalized the parent")
    report = dict(schema=1, job_id=os.environ["ASICJOBS_ID"], repository="flowkit-compat",
                  design="synthetic", top="payload",
                  manifest_sha256=os.environ["ASICJOBS_EXPECTED_MANIFEST_SHA256"],
                  request_sha256=os.environ["ASICJOBS_EXPECTED_REQUEST_SHA256"],
                  source_sha256=os.environ["ASICJOBS_EXPECTED_SOURCE_SHA256"],
                  checks=[dict(name="lifecycle", corner="synthetic", status="pass")])
    Path("report.json").write_text(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
