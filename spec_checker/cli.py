"""CLI for spec_checker — compare two specification sets via bounded model checking.

Usage:
    # Compare specs from a JSON file (uses ESBMC by default)
    python -m spec_checker compare specs.json

    # Compare with options
    python -m spec_checker compare specs.json --reference left --timeout 120 --emit-harness

    # Use CBMC instead
    python -m spec_checker compare specs.json --backend cbmc

    # Quick inline comparison for simple functions
    python -m spec_checker quick \\
        --signature "int gcd(int a, int b)" \\
        --left-post "__ret >= 1" \\
        --right-post "__ret >= 1" "__ret <= a" "__ret <= b"

    # Batch comparison from a directory of JSON files
    python -m spec_checker batch specs_dir/ --out results.json

See README.md for the JSON input schema and full documentation.
"""

import argparse
import json
import sys
import re
from pathlib import Path

from .types import CompareInput, CompareResult, FunctionSig, SpecSet, Param, Verdict
from .core import compare
from .backends.cbmc import CBMCBackend
from .backends.esbmc import ESBMCBackend


def _make_backend(args):
    """Create the verifier backend from CLI args."""
    if args.backend == "cbmc":
        return CBMCBackend(unwind=args.unwind)
    else:
        return ESBMCBackend(
            unwind=args.unwind,
            docker_path=args.docker_path,
            image=args.esbmc_image,
        )


def _result_to_dict(r: CompareResult) -> dict:
    def impl_dict(ir):
        d = {
            "verdict": ir.verdict.value,
            "vacuous": ir.vacuous,
        }
        if ir.witness:
            d["witness"] = ir.witness
        if ir.diagnostics:
            d["diagnostics"] = ir.diagnostics
        return d

    out = {
        "preconditions": {
            "left_implies_right": impl_dict(r.pre_left_implies_right),
            "right_implies_left": impl_dict(r.pre_right_implies_left),
        },
        "postconditions": {
            "left_implies_right": impl_dict(r.left_implies_right),
            "right_implies_left": impl_dict(r.right_implies_left),
        },
        "equivalent": r.equivalent,
    }
    if r.pre_soundness is not None:
        out["pre_soundness"] = impl_dict(r.pre_soundness)
    if r.pre_completeness is not None:
        out["pre_completeness"] = impl_dict(r.pre_completeness)
    if r.post_completeness is not None:
        out["post_completeness"] = impl_dict(r.post_completeness)
    if r.post_soundness is not None:
        out["post_soundness"] = impl_dict(r.post_soundness)
    out["verifier"] = r.verifier
    out["bounds"] = r.bounds
    return out


def _parse_signature(sig: str) -> FunctionSig:
    """Parse a C++ function signature string like 'int gcd(int a, int b)'."""
    m = re.match(r'([\w\s\*&:<>,]+?)\s+(\w+)\s*\(([^)]*)\)', sig.strip())
    if not m:
        raise ValueError(f"Cannot parse signature: {sig}")

    ret_type = m.group(1).strip()
    name = m.group(2).strip()
    params_str = m.group(3).strip()

    params = []
    if params_str:
        for p in params_str.split(','):
            p = p.strip()
            if not p:
                continue
            parts = p.rsplit(None, 1)
            if len(parts) == 2:
                ptype = parts[0].strip()
                pname = parts[1].replace('&', '').replace('*', '').strip()
                params.append(Param(name=pname, type=ptype))
            else:
                params.append(Param(name=f"arg{len(params)}", type=parts[0]))

    return FunctionSig(name=name, return_type=ret_type, params=params)


def cmd_compare(args):
    """Run a single comparison from a JSON file."""
    with open(args.input) as f:
        data = json.load(f)

    inp = CompareInput.from_dict(data)
    backend = _make_backend(args)

    work_dir = Path(args.work_dir) if args.work_dir else None

    result = compare(
        inp, backend,
        work_dir=work_dir,
        timeout=args.timeout,
        reference=args.reference,
        emit_harness=args.emit_harness,
    )

    out = _result_to_dict(result)
    print(json.dumps(out, indent=2))

    if args.out:
        with open(args.out, 'w') as f:
            json.dump(out, f, indent=2)


def cmd_quick(args):
    """Quick inline comparison for simple functions."""
    func = _parse_signature(args.signature)

    left = SpecSet(
        label="left",
        preconditions=args.left_pre or [],
        postconditions=args.left_post or [],
    )
    right = SpecSet(
        label="right",
        preconditions=args.right_pre or [],
        postconditions=args.right_post or [],
    )

    inp = CompareInput(function=func, left=left, right=right)
    backend = _make_backend(args)

    result = compare(
        inp, backend,
        timeout=args.timeout,
        reference=args.reference,
        emit_harness=args.emit_harness,
    )

    out = _result_to_dict(result)
    print(json.dumps(out, indent=2))


def cmd_batch(args):
    """Run comparison on a directory of JSON files."""
    input_dir = Path(args.input_dir)
    files = sorted(input_dir.glob("*.json"))

    if not files:
        print(f"No JSON files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    backend = _make_backend(args)
    all_results = []
    summary = {"total": 0, "equivalent": 0, "proved_both": 0,
               "errors": 0, "timeouts": 0}

    for i, fpath in enumerate(files):
        with open(fpath) as f:
            data = json.load(f)
        inp = CompareInput.from_dict(data)

        work_dir = Path(args.work_dir) / fpath.stem if args.work_dir else None

        result = compare(
            inp, backend,
            work_dir=work_dir,
            timeout=args.timeout,
            reference=args.reference,
            emit_harness=args.emit_harness,
        )

        summary["total"] += 1
        if result.equivalent:
            summary["equivalent"] += 1
        if (result.left_implies_right.verdict in (Verdict.ERROR, Verdict.TIMEOUT)
                or result.right_implies_left.verdict in (Verdict.ERROR, Verdict.TIMEOUT)):
            if result.left_implies_right.verdict == Verdict.TIMEOUT:
                summary["timeouts"] += 1
            else:
                summary["errors"] += 1

        lr = result.left_implies_right.verdict.value
        rl = result.right_implies_left.verdict.value
        eq_icon = "✅" if result.equivalent else "❌"
        print(f"[{i+1:3d}/{len(files)}] {eq_icon} {fpath.stem}: "
              f"L→R={lr} R→L={rl}")

        entry = {"file": fpath.stem, "function": inp.function.name}
        entry.update(_result_to_dict(result))
        all_results.append(entry)

    print(f"\n{'='*60}")
    print(f"Total: {summary['total']}  Equivalent: {summary['equivalent']}  "
          f"Errors: {summary['errors']}  Timeouts: {summary['timeouts']}")

    if args.out:
        with open(args.out, 'w') as f:
            json.dump({"summary": summary, "results": all_results}, f, indent=2)
        print(f"Results written to {args.out}")


def _add_common_args(p):
    """Add common arguments shared across subcommands."""
    p.add_argument("--backend", choices=["esbmc", "cbmc"], default="esbmc",
                   help="Verifier backend (default: esbmc)")
    p.add_argument("--timeout", type=int, default=60,
                   help="Per-query timeout in seconds (default: 60)")
    p.add_argument("--unwind", type=int, default=10,
                   help="Loop unwinding bound (default: 10)")
    p.add_argument("--emit-harness", action="store_true",
                   help="Keep generated harness .cpp files")
    p.add_argument("--docker-path", default="docker",
                   help="Path to docker binary (ESBMC only)")
    p.add_argument("--esbmc-image", default="esbmc:latest",
                   help="ESBMC Docker image name (default: esbmc:latest)")


def main():
    parser = argparse.ArgumentParser(
        prog="spec_checker",
        description="Compare two formal specification sets via bounded model checking.",
    )
    parser.add_argument("--version", action="version", version="spec_checker 0.2.0")

    sub = parser.add_subparsers(dest="command", required=True)

    # --- compare ---
    p_cmp = sub.add_parser("compare", help="Compare specs from a JSON file")
    p_cmp.add_argument("input", help="JSON file with comparison input")
    p_cmp.add_argument("--reference", choices=["left", "right"], default="left",
                       help="Which spec set is the reference (default: left)")
    p_cmp.add_argument("--work-dir", help="Directory for harness files")
    p_cmp.add_argument("--out", help="Write JSON results to file")
    _add_common_args(p_cmp)
    p_cmp.set_defaults(func=cmd_compare)

    # --- quick ---
    p_quick = sub.add_parser("quick", help="Quick inline comparison")
    p_quick.add_argument("--signature", required=True,
                         help='Function signature, e.g. "int gcd(int a, int b)"')
    p_quick.add_argument("--left-pre", nargs="*", help="Left preconditions")
    p_quick.add_argument("--left-post", nargs="*", help="Left postconditions")
    p_quick.add_argument("--right-pre", nargs="*", help="Right preconditions")
    p_quick.add_argument("--right-post", nargs="*", help="Right postconditions")
    p_quick.add_argument("--reference", choices=["left", "right"], default="left")
    _add_common_args(p_quick)
    p_quick.set_defaults(func=cmd_quick)

    # --- batch ---
    p_batch = sub.add_parser("batch", help="Batch compare a directory of JSON files")
    p_batch.add_argument("input_dir", help="Directory containing JSON spec files")
    p_batch.add_argument("--reference", choices=["left", "right"], default="left")
    p_batch.add_argument("--work-dir", help="Directory for harness files")
    p_batch.add_argument("--out", help="Write JSON results to file")
    _add_common_args(p_batch)
    p_batch.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
