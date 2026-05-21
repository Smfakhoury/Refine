"""Core comparison logic for spec_checker.

Orchestrates harness generation, verifier invocation, and result
interpretation to compare two specification sets.
"""

import re
from pathlib import Path
from .types import (
    CompareInput, CompareResult, ImplicationResult, Verdict,
)
from .harness import (
    build_implication_harness, build_vacuity_harness,
    _preprocess_expr, _map_result_alias, _extract_lambdas,
)
from .backends import VerifierBackend


def _prep_exprs(
    exprs: list[str],
    params: list,
    task_tag: str,
    extract_lambdas: bool = True,
) -> tuple[list[str], list[str]]:
    """Preprocess a list of spec expressions.

    Returns (processed_exprs, helper_functions).
    ESBMC supports lambdas natively, so lambda extraction can be skipped.
    """
    processed = []
    helpers = []
    for raw in exprs:
        e = _preprocess_expr(raw)
        if e is None:
            continue
        e = _map_result_alias(e, params)
        if extract_lambdas:
            e, h = _extract_lambdas(e, task_tag)
            helpers.extend(h)
        processed.append(e)
    return processed, helpers


def _check_implication(
    inp: CompareInput,
    assume_pre: list[str],
    assume_post: list[str],
    assert_post: list[str],
    helper_funcs: list[str],
    label: str,
    work_dir: Path,
    backend: VerifierBackend,
    timeout: int,
) -> ImplicationResult:
    """Run a single implication check and optional vacuity test."""
    result = ImplicationResult()

    if not assert_post:
        result.verdict = Verdict.PROVED
        result.diagnostics.append("No assertions to check — trivially true")
        return result

    if not assume_post and not assume_pre:
        # No assumptions — just check the assertions
        pass

    code = build_implication_harness(
        inp, assume_pre, assume_post, assert_post, helper_funcs, label,
        verifier=backend.name(),
    )
    path = work_dir / f"{label}.cpp"
    path.write_text(code)

    verdict, detail = backend.check_harness(path, timeout)

    if verdict == Verdict.PROVED:
        # Check vacuity: are assumptions contradictory?
        vac_code = build_vacuity_harness(
            inp, assume_pre, assume_post, helper_funcs,
            verifier=backend.name(),
        )
        vac_path = work_dir / f"{label}_vacuity.cpp"
        vac_path.write_text(vac_code)
        vac_verdict, _ = backend.check_harness(vac_path, timeout)

        if vac_verdict == Verdict.PROVED:
            result.verdict = Verdict.VACUOUS
            result.vacuous = True
            result.diagnostics.append("Assumptions are contradictory (vacuously true)")
        else:
            result.verdict = Verdict.PROVED
    elif verdict == Verdict.REFUTED:
        result.verdict = Verdict.REFUTED
        result.witness = detail
    elif verdict == Verdict.TIMEOUT:
        result.verdict = Verdict.TIMEOUT
        result.diagnostics.append("Verifier timed out")
    elif verdict == Verdict.ERROR:
        result.verdict = Verdict.ERROR
        result.diagnostics.append(detail or "Unknown verifier error")
    else:
        result.verdict = Verdict.UNKNOWN
        if detail:
            result.diagnostics.append(detail)

    return result


def compare(
    inp: CompareInput,
    backend: VerifierBackend,
    work_dir: Path | None = None,
    timeout: int = 60,
    reference: str = "left",
    emit_harness: bool = False,
) -> CompareResult:
    """Compare two spec sets using a bounded model checker.

    Args:
        inp: The comparison input (function sig, left specs, right specs).
        backend: The verifier backend to use.
        work_dir: Directory for generated harness files. Created if needed.
        timeout: Per-query timeout in seconds.
        reference: Which side is the reference ("left" or "right").
                   Used to set completeness/soundness aliases.
        emit_harness: If True, keep harness files; otherwise clean up.

    Returns:
        CompareResult with directional verdicts and optional
        completeness/soundness aliases.
    """
    if work_dir is None:
        work_dir = Path("/tmp/spec_checker_harnesses") / inp.function.name
    work_dir.mkdir(parents=True, exist_ok=True)

    tag = re.sub(r'\W', '_', inp.function.name)

    # ESBMC supports lambdas natively — skip extraction
    do_extract = backend.name() != "esbmc"

    # Preprocess all spec expressions
    left_pre, lp_helpers = _prep_exprs(inp.left.preconditions, inp.function.params, tag, do_extract)
    left_post, lpost_helpers = _prep_exprs(inp.left.postconditions, inp.function.params, tag, do_extract)
    right_pre, rp_helpers = _prep_exprs(inp.right.preconditions, inp.function.params, tag, do_extract)
    right_post, rpost_helpers = _prep_exprs(inp.right.postconditions, inp.function.params, tag, do_extract)

    all_helpers = list(inp.helpers) + lp_helpers + lpost_helpers + rp_helpers + rpost_helpers

    result = CompareResult(
        verifier=backend.name(),
        bounds={"unwind": getattr(backend, '_unwind', 20), "vec_max_size": 4},
    )

    # ---- Precondition checks ----
    # pre_left_implies_right: left_pre ⇒ right_pre
    #   "Does the GT precondition imply the inferred precondition?"
    #   If PROVED: inferred pre covers the full GT domain (pre-completeness when left=ref)
    if right_pre:
        result.pre_left_implies_right = _check_implication(
            inp,
            assume_pre=left_pre,
            assume_post=[],
            assert_post=right_pre,
            helper_funcs=all_helpers,
            label="pre_left_implies_right",
            work_dir=work_dir,
            backend=backend,
            timeout=timeout,
        )
    else:
        result.pre_left_implies_right = ImplicationResult(
            verdict=Verdict.PROVED,
            diagnostics=["No right preconditions — trivially implied"],
        )

    # pre_right_implies_left: right_pre ⇒ left_pre
    #   "Does the inferred precondition imply the GT precondition?"
    #   If PROVED: inferred pre doesn't accept inputs GT rejects (pre-soundness when left=ref)
    if left_pre:
        result.pre_right_implies_left = _check_implication(
            inp,
            assume_pre=right_pre,
            assume_post=[],
            assert_post=left_pre,
            helper_funcs=all_helpers,
            label="pre_right_implies_left",
            work_dir=work_dir,
            backend=backend,
            timeout=timeout,
        )
    else:
        result.pre_right_implies_left = ImplicationResult(
            verdict=Verdict.PROVED,
            diagnostics=["No left preconditions — trivially implied"],
        )

    # ---- Postcondition checks ----
    # Evaluate postconditions under the intersection of both preconditions.
    # This is the correct domain: we only compare behavior where both
    # specs agree the function should be callable.
    combined_pre = left_pre + right_pre

    # left_implies_right: combined_pre ∧ left_post → right_post
    #   "Does the GT postcondition imply the inferred postcondition?"
    result.left_implies_right = _check_implication(
        inp,
        assume_pre=combined_pre,
        assume_post=left_post,
        assert_post=right_post,
        helper_funcs=all_helpers,
        label="left_implies_right",
        work_dir=work_dir,
        backend=backend,
        timeout=timeout,
    )

    # right_implies_left: combined_pre ∧ right_post → left_post
    #   "Does the inferred postcondition imply the GT postcondition?"
    result.right_implies_left = _check_implication(
        inp,
        assume_pre=combined_pre,
        assume_post=right_post,
        assert_post=left_post,
        helper_funcs=all_helpers,
        label="right_implies_left",
        work_dir=work_dir,
        backend=backend,
        timeout=timeout,
    )

    # Equivalence: both preconditions and postconditions match in both directions
    result.equivalent = (
        result.left_implies_right.verdict == Verdict.PROVED
        and result.right_implies_left.verdict == Verdict.PROVED
        and result.pre_left_implies_right.verdict == Verdict.PROVED
        and result.pre_right_implies_left.verdict == Verdict.PROVED
    )

    # Set completeness/soundness aliases
    result.apply_roles(reference)

    if not emit_harness:
        # Clean up generated files
        for f in work_dir.glob("*.cpp"):
            f.unlink()
        try:
            work_dir.rmdir()
        except OSError:
            pass

    return result
