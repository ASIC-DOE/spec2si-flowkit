"""Small opt-in contract for engines running under a tracked parent."""
import os


def require_attached(recorder):
    """Fail before real work if a required semantic recorder silently disabled.

    runjob owns lifecycle. The engine enriches progress; never self-register a
    second lifecycle in this adapter mode. Existing jobrec defaults are unchanged.
    """
    job = os.environ.get("ASICJOBS_ID")
    directory = os.environ.get("ASICJOBS_JOBDIR")
    if (not job or not directory or not os.path.isdir(directory)
            or not getattr(recorder, "enabled", False) or not getattr(recorder, "attached", False)
            or recorder.jobid != job or not recorder.dir
            or os.path.realpath(recorder.dir) != os.path.realpath(directory)):
        raise RuntimeError("required recorder did not attach to the tracked parent")
    return recorder
