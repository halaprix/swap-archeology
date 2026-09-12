"""Build optional Cython performance probes outside the routing package.

Requires Cython and setuptools but does not add either project dependency:
``uv run --with cython --with setuptools python scripts/build_perf_native.py``.
Use ``--output-dir`` to rebuild elsewhere.  The default is
``.cache/perf-native``; this script writes generated C/C-extension files and a
manifest there only.  Generated functions keep Python objects for integer
arithmetic (``annotation_typing=False``, ``infer_types=False``), so they retain
the source models' unbounded-integer behavior and original exception classes.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import Cython
from Cython.Build import cythonize
from setuptools import Extension, setup

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src" / "swaparch"
DEFAULT_OUTPUT = PROJECT / ".cache" / "perf-native"
FLAGS = {"language_level": 3, "annotation_typing": False, "infer_types": False, "binding": True}


class Clean(ast.NodeTransformer):
    """Remove annotations only; they can trigger Cython integer specialization."""

    def __init__(self, *, remove_decorators: bool = False) -> None:
        self.remove_decorators = remove_decorators

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.returns = None
        if self.remove_decorators:
            node.decorator_list = []
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            argument.annotation = None
        return self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.stmt | None:
        if node.value is None:
            return None
        return ast.copy_location(ast.Assign(targets=[node.target], value=self.visit(node.value)), node)


class RenameWalk(ast.NodeTransformer):
    def __init__(self, version: str) -> None:
        self.version = version

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id == "m":
            node.id = "v3_math" if self.version == "v3" else "v4_math"
        elif node.id == "v3":
            node.id = "v3_math"
        return node


def write_module(output: Path, name: str, tree: ast.AST) -> Path:
    path = output / f"{name}.py"
    content = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
    if not path.exists() or path.read_text() != content:
        path.write_text(content)
    return path


def add_math_and_walks(output: Path, extensions: list[Extension], sources: dict[str, list[Path]]) -> None:
    for version in ("v3", "v4"):
        source = SOURCE / f"adapters/uniswap_{version}/math.py"
        tree = Clean().visit(ast.parse(source.read_text()))
        if version == "v3":
            tree.body = [node for node in tree.body if not (
                isinstance(node, ast.ClassDef) and node.name == "EvmRevert"
            )]
            tree.body.insert(2, ast.ImportFrom(
                module="swaparch.adapters.uniswap_v3.math", names=[ast.alias(name="EvmRevert")], level=0
            ))
        else:
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.level:
                    node.module, node.level = "swaparch.adapters.uniswap_v3", 0
        tree.body.insert(2, ast.Import(names=[ast.alias(name="cython")]))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                node.decorator_list.append(ast.Attribute(
                    value=ast.Name(id="cython", ctx=ast.Load()), attr="ccall", ctx=ast.Load()
                ))
        name = f"perf_math_{version}"
        path = write_module(output, name, tree)
        extensions.append(Extension(name, [str(path)]))
        sources[name] = [source]

    imports = ast.parse(
        "from swaparch.adapters.uniswap_v3 import math as v3_math\n"
        "from swaparch.adapters.uniswap_v4 import math as v4_math\n"
        "from swaparch.adapters.uniswap_v3.state import UniV3State\n"
        "from swaparch.adapters.uniswap_v4.state import UniV4State\n"
        "from swaparch.core.protocols import Unsupported"
    ).body
    walk_sources: list[Path] = []
    for version in ("v3", "v4"):
        source = SOURCE / f"adapters/uniswap_{version}/state.py"
        tree = ast.parse(source.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_swap")
        function.name = f"{version}_swap"
        imports.append(RenameWalk(version).visit(Clean().visit(function)))
        walk_sources.append(source)
    path = write_module(output, "perf_walks", ast.Module(body=imports, type_ignores=[]))
    extensions.append(Extension("perf_walks", [str(path)]))
    sources["perf_walks"] = walk_sources


def add_methods(output: Path, extensions: list[Extension], sources: dict[str, list[Path]]) -> dict[str, Any]:
    specs = (
        ("baseline", "swaparch.solver.baseline", "BaselineSolver"),
        ("search", "swaparch.solver.search", "GeneralSearchSolver"),
        ("evaluator", "swaparch.evaluator.evaluate", "Evaluator"),
        ("curve", "swaparch.adapters.curve_ng", "CurveNGState"),
        ("v3", "swaparch.adapters.uniswap_v3.state", "UniV3State"),
        ("v4", "swaparch.adapters.uniswap_v4.state", "UniV4State"),
    )
    index: dict[str, Any] = {}
    for short, module_name, class_name in specs:
        module = importlib.import_module(module_name)
        source = Path(module.__file__).resolve()
        tree = ast.parse(source.read_text())
        # Import original helpers and exceptions: generated methods preserve
        # exactly the source module's error types and constants.
        imports = ast.parse(
            f"from {module_name} import "
            + ", ".join(name for name in vars(module) if not name.startswith("__"))
        ).body
        functions: list[ast.stmt] = []
        methods: list[list[Any]] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for function in node.body:
                    if not isinstance(function, ast.FunctionDef) or function.name.startswith("__"):
                        continue
                    if any(isinstance(decorator, ast.Name) and decorator.id == "property"
                           for decorator in function.decorator_list):
                        continue
                    if short == "search" and function.name == "solve":
                        continue
                    static = any(isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
                                 for decorator in function.decorator_list)
                    original = function.name
                    function.name = f"{class_name}_{original}"
                    functions.append(Clean(remove_decorators=True).visit(function))
                    methods.append([original, function.name, static])
            elif short == "curve" and isinstance(node, ast.FunctionDef) and node.name != "_freeze":
                functions.append(Clean(remove_decorators=True).visit(node))
                methods.append([node.name, node.name, "module"])
        if not functions:
            raise RuntimeError(f"no methods selected for {module_name}.{class_name}")
        name = f"perf_methods_{short}"
        path = write_module(output, name, ast.Module(body=imports + functions, type_ignores=[]))
        extensions.append(Extension(name, [str(path)]))
        sources[name] = [source]
        index[name] = {"module": module_name, "class": class_name, "methods": methods}
    return index


def add_scopes(output: Path, extensions: list[Extension], sources: dict[str, list[Path]]) -> None:
    for short in ("solver", "state_memo"):
        source = PROJECT / "scripts" / f"perf_{short}.py"
        text = source.read_text().replace(
            'Path(__file__).with_name("eval_quote_performance.py")',
            repr(str(source.with_name("eval_quote_performance.py"))),
        )
        # Scope decorators are part of their behavior (notably contextmanager),
        # so Clean intentionally preserves them here.
        path = write_module(output, f"perf_scope_{short}", Clean().visit(ast.parse(text)))
        extensions.append(Extension(f"perf_scope_{short}", [str(path)]))
        sources[f"perf_scope_{short}"] = [source, source.with_name("eval_quote_performance.py")]


def manifest(output: Path, sources: dict[str, list[Path]]) -> None:
    file_hashes = {
        str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for paths in sources.values() for path in paths
    }
    file_hashes[str(Path(__file__).resolve().relative_to(PROJECT))] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    payload = {
        "python": sys.version,
        "cython": Cython.__version__,
        "compiler_directives": FLAGS,
        "modules": {name: [str(path.relative_to(PROJECT)) for path in paths]
                    for name, paths in sources.items()},
        "source_sha256": file_hashes,
    }
    (output / "manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build optional native performance probes")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # An interrupted rebuild must not look like a complete, validated build.
    (output / "manifest.json").unlink(missing_ok=True)
    extensions: list[Extension] = []
    sources: dict[str, list[Path]] = {}
    add_math_and_walks(output, extensions, sources)
    index = add_methods(output, extensions, sources)
    add_scopes(output, extensions, sources)
    (output / "method_index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    os.chdir(output)
    setup(name="swaparch-perf-native", ext_modules=cythonize(extensions, compiler_directives=FLAGS, quiet=True),
          script_args=["build_ext", "--inplace"])
    manifest(output, sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
