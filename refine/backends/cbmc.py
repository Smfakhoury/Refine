"""CBMC verifier backend."""

import shutil
import subprocess
from pathlib import Path
from ..types import Verdict
from . import BackendUnavailableError, VerifierBackend


class CBMCBackend(VerifierBackend):
    """Run CBMC natively (requires `cbmc` on PATH)."""

    def __init__(self, unwind: int = 20):
        self._unwind = unwind

    def name(self) -> str:
        return "cbmc"

    def preflight(self) -> None:
        if shutil.which("cbmc") is None:
            raise BackendUnavailableError(
                "cbmc not found on PATH. Install CBMC or use --backend esbmc."
            )

    def check_harness(self, harness_path: Path, timeout: int = 60) -> tuple[Verdict, str | None]:
        cmd = [
            "cbmc", str(harness_path),
            "--unwind", str(self._unwind),
            "--no-unwinding-assertions",
            "--no-standard-checks",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return Verdict.TIMEOUT, None
        except FileNotFoundError:
            return Verdict.ERROR, "cbmc not found on PATH"

        return self._interpret(output)

    @staticmethod
    def _interpret(output: str) -> tuple[Verdict, str | None]:
        if "PARSING ERROR" in output:
            lines = [l for l in output.split('\n')
                     if 'parse error' in l.lower() or 'error' in l.lower()]
            return Verdict.ERROR, '; '.join(lines[-3:])
        if "CONVERSION ERROR" in output:
            lines = [l for l in output.split('\n') if 'error' in l.lower()]
            return Verdict.ERROR, '; '.join(lines[-3:])
        if "VERIFICATION SUCCESSFUL" in output:
            return Verdict.PROVED, None
        if "VERIFICATION FAILED" in output:
            lines = [l for l in output.split('\n') if 'FAILURE' in l]
            return Verdict.REFUTED, '; '.join(lines[:5])
        return Verdict.UNKNOWN, output[-300:]
