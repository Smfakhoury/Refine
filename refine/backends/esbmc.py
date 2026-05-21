"""ESBMC verifier backend (via Docker)."""

import subprocess
from pathlib import Path
from ..types import Verdict
from . import VerifierBackend


class ESBMCBackend(VerifierBackend):
    """Run ESBMC via Docker (esbmc:latest image, linux/amd64)."""

    def __init__(
        self,
        unwind: int = 10,
        docker_path: str = "docker",
        image: str = "esbmc:latest",
    ):
        self._unwind = unwind
        self._docker_path = docker_path
        self._image = image

    def name(self) -> str:
        return "esbmc"

    def check_harness(
        self, harness_path: Path, timeout: int = 60
    ) -> tuple[Verdict, str | None]:
        harness_dir = str(harness_path.parent.resolve())
        harness_name = harness_path.name

        cmd = [
            self._docker_path,
            "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{harness_dir}:/work:ro",
            self._image,
            "/opt/esbmc/bin/esbmc", f"/work/{harness_name}",
            "--no-bounds-check", "--no-pointer-check",
            "--unwind", str(self._unwind),
            "--timeout", str(timeout),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout + 30,  # buffer for Docker overhead
            )
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return Verdict.TIMEOUT, None
        except FileNotFoundError:
            return Verdict.ERROR, f"docker not found at {self._docker_path}"

        return self._interpret(output)

    @staticmethod
    def _interpret(output: str) -> tuple[Verdict, str | None]:
        if "PARSING ERROR" in output or "ERROR: PARSING ERROR" in output:
            lines = [l for l in output.split('\n') if 'error:' in l.lower()]
            return Verdict.ERROR, '; '.join(lines[-3:])
        if "CONVERSION ERROR" in output:
            lines = [l for l in output.split('\n') if 'error' in l.lower()]
            return Verdict.ERROR, '; '.join(lines[-3:])
        if "VERIFICATION SUCCESSFUL" in output:
            return Verdict.PROVED, None
        if "VERIFICATION FAILED" in output:
            lines = [l for l in output.split('\n')
                     if 'Violated property' in l or 'FAILURE' in l]
            return Verdict.REFUTED, '; '.join(lines[:5])
        return Verdict.UNKNOWN, output[-300:]
