#!/usr/bin/env python3
"""Adapter: DeepTest specifications.db + FormalSpecCpp ground truth → refine JSON.

This adapter bridges DeepTest's spec inference output and FormalSpecCpp's
REQUIRE/ENSURE ground-truth format into the refine's generic JSON
input format.

Usage:
    # Convert a single function
    python adapters/deeptest_formalspec.py single \\
        --db .deeptest/analysis/specifications.db \\
        --gt-dir FormalSpecCpp-Dataset/FormalSpecCPP \\
        --nospec-dir FormalSpecCpp-Dataset/FormalSpecCPP-NoSpec \\
        --task task_id_101 \\
        --out specs/task_id_101.json

    # Convert all tasks into a batch directory
    python adapters/deeptest_formalspec.py batch \\
        --db .deeptest/analysis/specifications.db \\
        --gt-dir FormalSpecCpp-Dataset/FormalSpecCPP \\
        --nospec-dir FormalSpecCpp-Dataset/FormalSpecCPP-NoSpec \\
        --out-dir specs/

    # Then run refine batch on the output
    python -m refine batch specs/ --out results.json
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Spec extraction: FormalSpecCpp ground truth (REQUIRE / ENSURE)
# ---------------------------------------------------------------------------

def _balanced_extract(text: str, start: int) -> str | None:
    """Extract content between balanced delimiters."""
    opener = text[start]
    closer = ')' if opener == '(' else '}'
    depth = 0
    i = start
    while i < len(text):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return None


def extract_gt_specs(filepath: str) -> tuple[list[str], list[str], list[str]]:
    """Extract REQUIRE/ENSURE specs and helper functions from a GT file.

    Returns (preconditions, postconditions, helper_function_definitions).
    """
    with open(filepath) as f:
        text = f.read()

    pre, post = [], []

    for match in re.finditer(r'\b(REQUIRE|ENSURE)\s*\(', text):
        macro = match.group(1)
        line_start = text.rfind('\n', 0, match.start()) + 1
        line = text[line_start:text.find('\n', match.end())]
        if '#define' in line:
            continue
        paren_start = match.end() - 1
        expr = _balanced_extract(text, paren_start)
        if expr is None:
            continue
        expr = expr.strip()
        if expr in ('true', 'cond'):
            continue
        if macro == 'REQUIRE':
            pre.append(expr)
        else:
            post.append(expr)

    # Standalone asserts (loop invariants)
    for match in re.finditer(r'\bassert\s*\(', text):
        line_start = text.rfind('\n', 0, match.start()) + 1
        line_end = text.find('\n', match.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        if any(kw in line for kw in ('REQUIRE', 'ENSURE', '#define', 'DT spec', '#include')):
            continue
        paren_start = match.end() - 1
        expr = _balanced_extract(text, paren_start)
        if expr and expr.strip() not in ('cond', 'true', 'false'):
            post.append(expr.strip())

    # Extract helper functions via clang AST
    helpers = _clang_extract_helpers(filepath)

    return pre, post, helpers


def _clang_extract_helpers(filepath: str) -> list[str]:
    """Use clang AST dump to identify helper function definitions."""
    try:
        result = subprocess.run(
            ["clang++", "-Xclang", "-ast-dump=json", "-fsyntax-only", filepath],
            capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        ast_data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []

    src_file = os.path.basename(filepath)

    def is_user_loc(node):
        loc = node.get('loc', {})
        if 'file' in loc and src_file in loc['file']:
            return True
        sl = loc.get('spellingLoc', {})
        if 'file' in sl and src_file in sl['file']:
            return True
        return False

    user_funcs = []
    for child in ast_data.get('inner', []):
        if isinstance(child, dict) and child.get('kind') == 'FunctionDecl':
            if is_user_loc(child):
                name = child.get('name', '')
                if name and name != 'main':
                    user_funcs.append(child)

    if len(user_funcs) <= 1:
        return []

    with open(filepath) as f:
        source_lines = f.read().split('\n')

    helpers = []
    for func_node in user_funcs[:-1]:
        rng = func_node.get('range', {})
        begin_line = rng.get('begin', {}).get('line', 0)
        end_line = rng.get('end', {}).get('line', 0)
        if begin_line > 0 and end_line >= begin_line:
            func_text = '\n'.join(source_lines[begin_line - 1:end_line])
            func_text = re.sub(r'\bREQUIRE\s*\([^;]*\)\s*;[^\n]*', '', func_text)
            func_text = re.sub(r'\bENSURE\s*\([^;]*\)\s*;[^\n]*', '', func_text)
            func_text = re.sub(r'\bassert\s*\([^;]*\)\s*;[^\n]*', '', func_text)
            func_text = re.sub(r'//[^\n]*', '', func_text)
            helpers.append(func_text.strip())

    return helpers


# ---------------------------------------------------------------------------
# Spec extraction: DeepTest specifications.db
# ---------------------------------------------------------------------------

def extract_dt_specs(
    db_path: str,
    task_filename: str,
) -> tuple[list[str], list[str]]:
    """Extract DeepTest pre/postconditions for a file from specifications.db.

    Args:
        db_path: Path to .deeptest/analysis/specifications.db
        task_filename: Source filename (e.g. "task_id_101.cpp")

    Returns:
        (preconditions, postconditions)
    """
    db = sqlite3.connect(db_path)
    c = db.cursor()

    rows = c.execute("""
        SELECT s.direction, s.expression
        FROM specifications s
        JOIN functions f ON s.function_id = f.id
        WHERE f.file LIKE ?
        ORDER BY s.id
    """, (f"%{task_filename}",)).fetchall()

    pre = [r[1] for r in rows if r[0] == 'pre']
    post = [r[1] for r in rows if r[0] == 'post']
    db.close()
    return pre, post


# ---------------------------------------------------------------------------
# Function signature extraction from NoSpec files
# ---------------------------------------------------------------------------

def parse_function_sig(filepath: str) -> tuple[str, str, list[dict]] | None:
    """Parse the main function signature from a NoSpec file.

    Returns (name, return_type, [{"name": ..., "type": ...}]) or None.
    """
    with open(filepath) as f:
        text = f.read()

    text_nc = re.sub(r'//.*', '', text)
    text_nc = re.sub(r'/\*.*?\*/', '', text_nc, flags=re.DOTALL)
    text_nc = re.sub(r'#include\s*<[^>]+>', '', text_nc)
    text_nc = re.sub(r'#include\s*"[^"]+"', '', text_nc)

    skip = {'if', 'while', 'for', 'switch', 'else', 'return', 'do', 'main', 'assert'}

    functions = []
    for m in re.finditer(
        r'(?:^|\n)\s*'
        r'((?:(?:const|static|inline|unsigned|signed|long|short|struct|class|virtual)\s+)*'
        r'(?:std::)?[\w:<>,\s\*&]+?)\s+'
        r'(\w+)\s*'
        r'\(([^)]*)\)\s*(?:const\s*)?(?:override\s*)?\{',
        text_nc):
        ret_type = m.group(1).strip()
        func_name = m.group(2).strip()
        params_str = m.group(3).strip()

        if func_name in skip or ret_type in skip:
            continue

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
                    params.append({"name": pname, "type": ptype})
                elif len(parts) == 1:
                    params.append({"name": f"arg{len(params)}", "type": parts[0]})

        functions.append((func_name, ret_type, params))

    if not functions:
        return None
    return functions[-1]  # Last non-main function


def extract_local_vars(filepath: str) -> list[dict]:
    """Extract local variable declarations from a NoSpec file via clang AST."""
    try:
        result = subprocess.run(
            ["clang++", "-Xclang", "-ast-dump=json", "-fsyntax-only", filepath],
            capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        ast_data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []

    src_file = os.path.basename(filepath)
    user_funcs = []

    def is_user_loc(node):
        loc = node.get('loc', {})
        if 'file' in loc and src_file in loc['file']:
            return True
        sl = loc.get('spellingLoc', {})
        if 'file' in sl and src_file in sl['file']:
            return True
        return False

    for child in ast_data.get('inner', []):
        if isinstance(child, dict) and child.get('kind') == 'FunctionDecl':
            if is_user_loc(child):
                user_funcs.append(child)

    if not user_funcs:
        return []

    func = user_funcs[-1]
    locals_found = []

    def find_var_decls(node):
        if not isinstance(node, dict):
            return
        if node.get('kind') == 'DeclStmt':
            for inner in node.get('inner', []):
                if inner.get('kind') == 'VarDecl':
                    name = inner.get('name', '')
                    qtype = inner.get('type', {}).get('qualType', '')
                    if name and name not in ('i', 'j', 'k'):
                        locals_found.append({"name": name, "type": qtype})
        for v in node.get('inner', []):
            find_var_decls(v)

    for inner in func.get('inner', []):
        if inner.get('kind') == 'CompoundStmt':
            find_var_decls(inner)

    return locals_found


# ---------------------------------------------------------------------------
# Build refine JSON input
# ---------------------------------------------------------------------------

def build_compare_input(
    task_id: str,
    gt_dir: str,
    nospec_dir: str,
    db_path: str,
) -> dict | None:
    """Build a refine CompareInput dict for one task.

    Returns None if required files are missing or no specs found.
    """
    gt_file = os.path.join(gt_dir, f"{task_id}.cpp")
    nospec_file = os.path.join(nospec_dir, f"{task_id}.cpp")

    if not os.path.exists(gt_file) or not os.path.exists(nospec_file):
        return None

    # Parse function signature
    sig = parse_function_sig(nospec_file)
    if sig is None:
        return None
    func_name, ret_type, params = sig

    # Extract specs
    gt_pre, gt_post, helpers = extract_gt_specs(gt_file)
    dt_pre, dt_post = extract_dt_specs(db_path, f"{task_id}.cpp")

    if not gt_post:
        return None

    # Extract local variables
    local_vars = extract_local_vars(nospec_file)

    return {
        "schema_version": "1.0",
        "function": {
            "name": func_name,
            "return_type": ret_type,
            "params": params,
            "local_vars": local_vars,
        },
        "left": {
            "label": "ground_truth",
            "preconditions": gt_pre,
            "postconditions": gt_post,
        },
        "right": {
            "label": "deeptest_inferred",
            "preconditions": dt_pre,
            "postconditions": dt_post,
        },
        "helpers": helpers,
        "options": {
            "task_id": task_id,
            "source": "deeptest_formalspec_adapter",
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_single(args):
    result = build_compare_input(args.task, args.gt_dir, args.nospec_dir, args.db)
    if result is None:
        print(f"Could not build input for {args.task}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Written to {args.out}", file=sys.stderr)


def cmd_batch(args):
    nospec_dir = Path(args.nospec_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    task_ids = sorted(set(f.stem for f in nospec_dir.glob("task_id_*.cpp")))
    written = 0

    for task_id in task_ids:
        result = build_compare_input(task_id, args.gt_dir, args.nospec_dir, args.db)
        if result is None:
            continue

        out_path = out_dir / f"{task_id}.json"
        with open(out_path, 'w') as f:
            json.dump(result, f, indent=2)
        written += 1

    print(f"Wrote {written}/{len(task_ids)} spec comparison files to {out_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert DeepTest + FormalSpecCpp specs to refine JSON format",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # single
    p_single = sub.add_parser("single", help="Convert one task")
    p_single.add_argument("--db", required=True,
                          help="Path to DeepTest specifications.db")
    p_single.add_argument("--gt-dir", required=True,
                          help="FormalSpecCpp ground truth directory")
    p_single.add_argument("--nospec-dir", required=True,
                          help="FormalSpecCpp NoSpec directory")
    p_single.add_argument("--task", required=True,
                          help="Task ID (e.g. task_id_101)")
    p_single.add_argument("--out", help="Output JSON file")
    p_single.set_defaults(func=cmd_single)

    # batch
    p_batch = sub.add_parser("batch", help="Convert all tasks")
    p_batch.add_argument("--db", required=True)
    p_batch.add_argument("--gt-dir", required=True)
    p_batch.add_argument("--nospec-dir", required=True)
    p_batch.add_argument("--out-dir", required=True,
                         help="Output directory for JSON files")
    p_batch.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
