# Refine: Bounded Refinement Checking for Inferred C++ Specifications

`refine` takes two sets of pre/postconditions for the same function and checks whether they are logically equivalent, or which direction of implication holds. It uses bounded model checkers (ESBMC or CBMC) to verify the implications, extracts concrete counterexamples when checks fail, and optionally generates plain-English explanations of failures via the Copilot CLI.

## When to use this tool

- Comparing inferred specifications against ground-truth specifications
- Validating that a refactored contract is equivalent to the original
- Checking if any spec refines another (weaker precondition, stronger postcondition)
- Auditing LLM-inferred specs with concrete counterexamples and NL explanations

## Installation

Requires Python ≥ 3.10 and one of:

- **ESBMC** (default, recommended): Docker with an `esbmc:latest` image
- **CBMC**: Native install via Homebrew (`brew install cbmc`) or [diffblue/cbmc](https://github.com/diffblue/cbmc)

Optional:
- **Copilot CLI** (`copilot` on PATH): Required for `--interpret` (NL explanations of counterexamples)

No Python dependencies beyond the standard library.

## Quick start

```bash
# Compare two specs inline
python -m refine quick \
  --signature "int gcd(int a, int b)" \
  --left-pre "a > 0" "b > 0" \
  --left-post "__ret >= 1" "__ret <= a" "__ret <= b" \
  --right-post "__ret >= 1"

# Compare from a JSON file
python -m refine compare specs.json

# Batch compare a directory of JSON files
python -m refine batch specs_dir/ --out results.json

# With NL interpretation of counterexamples
python -m refine compare specs.json --interpret
```

## Input format

The JSON input schema (v1.0):

```json
{
  "schema_version": "1.0",
  "function": {
    "name": "KthElement",
    "return_type": "int",
    "params": [
      {"name": "arr", "type": "const std::vector<int>&"},
      {"name": "k", "type": "size_t"}
    ],
    "local_vars": [
      {"name": "result", "type": "int"}
    ]
  },
  "left": {
    "label": "ground_truth",
    "preconditions": ["k >= 1 && k <= arr.size()"],
    "postconditions": ["result == arr[k - 1]"]
  },
  "right": {
    "label": "inferred",
    "preconditions": ["k >= 1 && k <= arr.size()"],
    "postconditions": ["result == arr[k - 1]"]
  },
  "helpers": [],
  "options": {}
}
```

- **`left` / `right`**: Neutral names. Use `--reference left` (default) to designate the left side as ground truth.
- **`helpers`**: Optional list of C++ helper function definitions needed by the specs.
- Spec expressions use C++ syntax. `result` is automatically mapped to the return value.

## What it checks

### Postconditions (under the intersection of both preconditions)

| Check | Encoding | Meaning |
|---|---|---|
| `left_implies_right` | `pre_L ∧ pre_R ∧ post_L ⇒ post_R` | Left postcondition implies right |
| `right_implies_left` | `pre_L ∧ pre_R ∧ post_R ⇒ post_L` | Right postcondition implies left |

### Preconditions

| Check | Encoding | Meaning |
|---|---|---|
| `pre_left_implies_right` | `pre_L ⇒ pre_R` | Left precondition implies right |
| `pre_right_implies_left` | `pre_R ⇒ pre_L` | Right precondition implies left |

### Domain aliases (when `--reference left`)

| Alias | Maps to | Interpretation |
|---|---|---|
| `post_soundness` | `left_implies_right` | Inferred postcondition doesn't overclaim |
| `post_completeness` | `right_implies_left` | Inferred postcondition captures everything |
| `pre_soundness` | `pre_right_implies_left` | Inferred precondition doesn't accept invalid inputs |
| `pre_completeness` | `pre_left_implies_right` | Inferred precondition covers the full valid domain |

**Equivalent** = all four checks proved (mutual refinement).

### Verdicts

| Verdict | Meaning |
|---|---|
| `proved` | Implication holds (within bounds) |
| `refuted` | Counterexample found |
| `vacuous` | Assumptions are contradictory — implication holds trivially |
| `unknown` | Verifier could not determine |
| `error` | Parse/conversion error in harness |
| `timeout` | Verifier timed out |

### Vacuity detection

Every `proved` result is automatically checked for vacuity: if the assumptions are contradictory (unsatisfiable), the result is reported as `vacuous` instead of `proved`.

## CLI reference

```
python -m refine compare INPUT.json [options]
python -m refine quick --signature SIG [options]
python -m refine batch INPUT_DIR/ [options]
python -m refine selftest
python -m refine tui [start_dir]

Options:
  --backend {esbmc,cbmc}     Verifier backend (default: esbmc)
  --reference {left,right}   Which side is ground truth (default: left)
  --timeout SECONDS          Per-query timeout (default: 60)
  --unwind N                 Loop unwinding bound (default: 10)
  --vec-size N               Maximum vector size for nondet vectors (default: 4)
  --auto-bounds              Infer bounds from spec text (e.g. size constraints)
  --validate-bounds          Re-run proved results at 2× bounds; downgrade to
                             unknown if the verdict flips
  --interpret                Generate NL explanations of counterexamples via
                             the Copilot CLI
  --emit-harness             Keep generated .cpp harness files
  --work-dir DIR             Directory for harness files
  --out FILE                 Write JSON results to file
  --docker-path PATH         Path to docker binary (ESBMC only)
  --esbmc-image IMAGE        ESBMC Docker image (default: esbmc:latest)
```

## Interactive TUI

Launch the terminal UI for a visual, interactive experience:

```bash
python -m refine tui              # browse from current directory
python -m refine tui specs_dir/   # start in a specific directory
```

The TUI provides:
- **File browser** — navigate the filesystem to select JSON spec files or directories
- **Spec preview** — review function signatures and spec sets before running
- **Options editor** — configure backend, timeout, bounds, and other settings interactively
- **Live progress** — animated spinner with phase indicators during verification
- **Results dashboard** — color-coded verdict table with expandable counterexamples
- **Batch results** — summary table with progress bar for directory-wide comparisons
- **JSON export** — save results to file from within the TUI

Keyboard shortcuts: `↑`/`↓` navigate, `Enter` selects, `Esc` goes back, `q` quits from the main menu.

## Example output

Running `refine` on `AppendArrayToSeq` — ground-truth (left) vs. LLM-inferred (right) specs — with `--interpret`:

```bash
python -m refine compare task_id_106.json --interpret
```

```json
{
  "equivalent": false,
  "pre_soundness": {
    "verdict": "refuted",
    "counterexample": {
      "s._data": "{ ._data=&s_data_arr[0], ._size=4 }",
      "a._data": "{ ._data=&a_data_arr[0], ._size=0 }",
      "__violated": "!a.empty()"
    }
  },
  "pre_completeness": {
    "verdict": "proved",
    "diagnostics": ["No right preconditions — trivially implied"]
  },
  "post_soundness": {
    "verdict": "refuted",
    "counterexample": {
      "s._data": "{ ._data=&s_data_arr[0], ._size=4 }",
      "a._data": "{ ._data=&a_data_arr[0], ._size=4 }",
      "r": "{ ._data=0, ._size=8 }",
      "__violated": "std::equal(r.begin() + (s.size()), r.end(), a.begin())"
    }
  },
  "post_completeness": {
    "verdict": "refuted",
    "counterexample": {
      "s._data": "{ ._data=&s_data_arr[0], ._size=2 }",
      "a._data": "{ ._data=&a_data_arr[0], ._size=2 }",
      "r": "{ ._data=0, ._size=4 }",
      "__violated": "r[s.size() + j] == a[j]"
    }
  },
  "interpretations": {
    "post_soundness": "The counterexample shows that when s and a each have size 4,
      the ground-truth postconditions are satisfied for a return vector of size 8,
      yet std::equal over the second half fails — meaning the candidate's use of
      std::equal is too strong, imposing a stricter equality check than the
      ground-truth's per-element index specifications.",
    "post_completeness": "The candidate postcondition r[s.size() + j] == a[j] is
      too strong: it asserts element-wise equality for an index j that is left
      unconstrained, so the model checker picks j outside the valid range.",
    "pre_soundness": "The counterexample passes an empty array (a._size=0), which
      is a valid input. The candidate precondition !a.empty() is too strong: it
      rejects empty arrays when the ground truth imposes no such restriction."
  }
}
```

The `interpretations` field (enabled by `--interpret`) provides plain-English summaries of each refuted check, explaining what the counterexample demonstrates and whether the candidate spec is too strong, too weak, or incomparable.

## Architecture

```
refine/
├── types.py          # Data classes: CompareInput, CompareResult, Verdict, ...
├── harness.py        # Harness generation (STL stubs, nondet vars, assume/assert)
│                     # Also: infer_bounds() for auto-bounds from spec text
├── core.py           # Orchestration: prep specs → build harness → run verifier
│                     # Also: validate_bounds (iterative deepening at 2× bounds)
├── interpret.py      # NL interpretation of counterexamples via Copilot CLI
├── tui.py            # Interactive terminal UI (curses-based, zero dependencies)
├── cli.py            # CLI entry point (compare, quick, batch, selftest, tui)
├── backends/
│   ├── __init__.py   # VerifierBackend ABC
│   ├── cbmc.py       # CBMC backend (native)
│   └── esbmc.py      # ESBMC backend (via Docker), counterexample extraction
└── stubs/
    ├── cbmc_stl.hpp  # Minimal STL stubs for CBMC
    └── esbmc_stl.hpp # Minimal STL stubs for ESBMC
```

### How it works

1. **Parse input** — Load function signature + two spec sets from JSON or CLI args
2. **Preprocess** — Normalize expressions, extract lambdas (CBMC only)
3. **Infer bounds** (optional) — Scan spec text for size constraints to set vector/unwind bounds
4. **Generate harnesses** — For each implication direction, emit a self-contained C++ file with STL stubs, nondet variable declarations, assumptions, and assertions
5. **Run verifier** — Invoke ESBMC or CBMC on each harness
6. **Interpret results** — Map verifier output to verdicts, check for vacuity, extract counterexamples
7. **Validate bounds** (optional) — Re-run proved checks at 2× bounds; downgrade if verdicts flip
8. **NL interpretation** (optional) — Send counterexamples to the Copilot CLI for plain-English summaries
9. **Report** — Emit structured JSON with directional results, domain aliases, witnesses, and interpretations


## Limitations

- **Bounded verification**: Results are sound within the configured unwind/vector bounds, not universally. A `proved` verdict means "no counterexample within bounds." Use `--validate-bounds` to detect instability.
- **STL stubs**: Only `vector`, `pair`, and common algorithms are stubbed. Programs using `map`, `set`, `string`, etc. will get `unsupported` verdicts.
- **Lambda support**: ESBMC handles lambdas natively. CBMC requires lambda extraction to named functions (automatic but imperfect).
- **No execution model**: Specs are compared as pure logical predicates. The checker does not model actual function execution — it checks implication between spec expressions.
- **NL interpretation**: Requires the `copilot` CLI on PATH with valid authentication. Quality depends on the LLM's understanding of formal verification concepts.

## License

MIT
