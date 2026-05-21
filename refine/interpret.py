"""Natural-language interpretation of refinement check results.

When a soundness or completeness check is violated (REFUTED), generates
a human-readable summary explaining what the counterexample means and
why the specs differ. Uses an LLM via the Copilot CLI (same approach
as DeepTest's llm_infer.py).
"""

import os
import subprocess
import shutil
from .types import CompareInput, CompareResult, ImplicationResult, Verdict


# Token env vars, matching DeepTest's convention.
_TOKEN_ENV_VARS = (
    "COPILOT_GITHUB_TOKEN",
    "DEEPTEST_COPILOT_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


def _build_copilot_env() -> dict:
    """Return env dict with Copilot token propagated under all known names."""
    env = os.environ.copy()
    token = next(
        (env[v].strip() for v in _TOKEN_ENV_VARS if env.get(v, "").strip()),
        "",
    )
    if token:
        for var in _TOKEN_ENV_VARS:
            env[var] = token
    return env


def _build_prompt(
    check_name: str,
    func_name: str,
    signature: str,
    gt_specs: list[str],
    dt_specs: list[str],
    counterexample: dict[str, str],
    direction: str,
) -> str:
    """Build a concise prompt for the Copilot CLI."""
    cex_str = ", ".join(f"{k}={v}" for k, v in counterexample.items())
    violated = counterexample.get("__violated", "")

    return (
        "You are a formal verification expert. "
        "Given two sets of specifications (ground-truth and candidate) for a "
        "C/C++ function, and a counterexample from a bounded model checker, "
        "explain in 1-2 plain-English sentences: "
        "(1) what the counterexample demonstrates, "
        "(2) why the candidate spec is wrong (too strong, too weak, or different). "
        "Do NOT repeat the spec text verbatim. Be concrete about the values.\n\n"
        f"Function: {signature}\n"
        f"Check: {check_name} ({direction})\n"
        f"Ground-truth specs: {'; '.join(gt_specs)}\n"
        f"Candidate specs: {'; '.join(dt_specs)}\n"
        f"Counterexample: {cex_str}\n"
        f"Violated assertion: {violated}\n\n"
        "Explain in 1-2 sentences what this counterexample means for the "
        "candidate spec quality."
    )


def _call_llm(prompt: str, timeout: int = 120) -> str | None:
    """Call the Copilot CLI to interpret a counterexample.

    Uses ``copilot -p <prompt> -s --no-ask-user --allow-all``, the same
    invocation pattern as DeepTest's spec-inference skill.
    """
    copilot = shutil.which("copilot")
    if copilot is None:
        return None

    try:
        result = subprocess.run(
            [copilot, "-p", prompt, "-s", "--no-ask-user", "--allow-all"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_build_copilot_env(),
        )
        if result.returncode == 0 and result.stdout and result.stdout.strip():
            return result.stdout.strip()
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            import sys
            print(f"Warning: copilot CLI returned exit code {result.returncode}",
                  file=sys.stderr)
            if stderr:
                print(f"  stderr: {stderr[:400]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        import sys
        print(f"Warning: copilot CLI timed out after {timeout}s", file=sys.stderr)
    except FileNotFoundError:
        return None
    except Exception as e:
        import sys
        print(f"Warning: copilot CLI error: {e}", file=sys.stderr)

    return None


def interpret_result(
    inp: CompareInput,
    result: CompareResult,
    reference: str = "left",
) -> dict[str, str]:
    """Generate NL summaries for all REFUTED checks in a CompareResult.

    Returns a dict mapping check names to interpretation strings.
    Only includes checks that have counterexamples.
    """
    if reference == "left":
        gt_pre, gt_post = inp.left.preconditions, inp.left.postconditions
        dt_pre, dt_post = inp.right.preconditions, inp.right.postconditions
    else:
        gt_pre, gt_post = inp.right.preconditions, inp.right.postconditions
        dt_pre, dt_post = inp.left.preconditions, inp.left.postconditions

    sig = (f"{inp.function.return_type} {inp.function.name}("
           + ", ".join(f"{p.type} {p.name}" for p in inp.function.params)
           + ")")

    checks = [
        ("post_soundness", result.post_soundness,
         gt_post, dt_post, "GT_post ⇒ DT_post (does DT overclaim?)"),
        ("post_completeness", result.post_completeness,
         dt_post, gt_post, "DT_post ⇒ GT_post (does DT miss behavior?)"),
        ("pre_soundness", result.pre_soundness,
         dt_pre, gt_pre, "DT_pre ⇒ GT_pre (does DT accept invalid inputs?)"),
        ("pre_completeness", result.pre_completeness,
         gt_pre, dt_pre, "GT_pre ⇒ DT_pre (does DT reject valid inputs?)"),
    ]

    interpretations = {}
    for name, check, assume_specs, assert_specs, direction in checks:
        if check is None or check.verdict != Verdict.REFUTED:
            continue
        if not check.counterexample:
            continue

        prompt = _build_prompt(
            name, inp.function.name, sig,
            assume_specs, assert_specs,
            check.counterexample, direction,
        )

        interp = _call_llm(prompt)
        if interp:
            interpretations[name] = interp

    return interpretations
