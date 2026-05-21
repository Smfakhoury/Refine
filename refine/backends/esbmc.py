"""ESBMC verifier backend (via Docker)."""

import re
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
            witness = ESBMCBackend._extract_counterexample(output)
            return Verdict.REFUTED, witness
        return Verdict.UNKNOWN, output[-300:]

    @staticmethod
    def _extract_counterexample(output: str) -> str:
        """Extract a human-readable counterexample from ESBMC output.

        Parses the [Counterexample] trace to extract variable assignments
        in main(), filtering out internal/pointer plumbing.
        """
        lines = output.split('\n')

        # Find the counterexample section
        cex_start = None
        for i, line in enumerate(lines):
            if '[Counterexample]' in line:
                cex_start = i + 1
                break

        if cex_start is None:
            return "Violated property (no counterexample trace)"

        # Parse State entries: variable = value
        assignments = {}
        violated_prop = None
        skip_internals = {'_data_arr'}

        i = cex_start
        while i < len(lines):
            line = lines[i].strip()

            # Violated property block
            if line == 'Violated property:':
                # Next few lines have file/line and the assertion text
                prop_lines = []
                for j in range(i + 1, min(i + 4, len(lines))):
                    l = lines[j].strip()
                    if l.startswith('assertion '):
                        violated_prop = l[len('assertion '):]
                    elif l and not l.startswith('file ') and not l.startswith('return_value'):
                        prop_lines.append(l)
                break

            # State assignment line: varname = value
            if line.startswith('State ') and 'function main' in line:
                # Look for the assignment on the line after the separator
                if i + 2 < len(lines) and '----' in lines[i + 1]:
                    assign_line = lines[i + 2].strip()
                    m = re.match(r'(\w[\w.]*)\s*=\s*(.+)', assign_line)
                    if m:
                        var = m.group(1)
                        val = m.group(2).strip()
                        # Skip internal plumbing
                        if not any(s in var for s in skip_internals):
                            # Clean up: strip bit patterns like "(00000000 ...)"
                            if len(val) < 500:
                                val = re.sub(r'\s*\([01]+(?:\s[01]+)*\)\s*$', '', val)
                            # Skip struct assignments with nil pointers
                            if 'pointer_object=nil' not in val:
                                assignments[var] = val
            i += 1

        # Build readable output
        parts = []
        if assignments:
            parts.append("Counterexample:")
            for var, val in assignments.items():
                parts.append(f"  {var} = {val}")
        if violated_prop:
            parts.append(f"Violated: {violated_prop}")

        return '\n'.join(parts) if parts else "Violated property (could not parse trace)"
