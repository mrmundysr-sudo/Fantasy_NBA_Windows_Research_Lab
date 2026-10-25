"""Safe numeric expression evaluator used for metric formulas and user custom formulas.

Only arithmetic, a few math helpers and named variables are allowed. No attribute
access, subscripts, comprehensions, imports or arbitrary calls are possible, so user
or AI-supplied formulas cannot execute code. Division by zero yields NaN (missing),
never inf and never an exception.
"""
from __future__ import annotations

import ast
import math
from typing import Any, Dict, Iterable, Set

import numpy as np

MAX_LEN = 600
MAX_NODES = 250


class FormulaError(ValueError):
    pass


def _div(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(b == 0, np.nan, a / np.where(b == 0, 1.0, b))
    return out


def _pow(a, b):
    a = np.asarray(a, dtype=float)
    if np.ndim(b) != 0 or abs(float(b)) > 6:
        raise FormulaError("exponent must be a constant with |n| <= 6")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.power(a, float(b))


def _where_pos(x, a, b):
    return np.where(np.asarray(x, dtype=float) > 0, a, b)


FUNCS = {
    "abs": np.abs,
    "sqrt": lambda x: np.sqrt(np.where(np.asarray(x, dtype=float) < 0, np.nan, x)),
    "log": lambda x: np.log(np.where(np.asarray(x, dtype=float) <= 0, np.nan, x)),
    "exp": lambda x: np.exp(np.clip(np.asarray(x, dtype=float), -50, 50)),
    "min": np.minimum,
    "max": np.maximum,
    "if_pos": _where_pos,
}

_BIN = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: _div,
    ast.Pow: _pow,
}


def parse(expr: str) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError("empty formula")
    if len(expr) > MAX_LEN:
        raise FormulaError("formula too long")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"syntax error: {e.msg}") from None
    n = 0
    for node in ast.walk(tree):
        n += 1
        if n > MAX_NODES:
            raise FormulaError("formula too complex")
        if isinstance(node, (ast.Expression, ast.Load, ast.operator, ast.unaryop)):
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            continue
        if isinstance(node, ast.Name):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS and not node.keywords:
            continue
        raise FormulaError(f"disallowed syntax: {type(node).__name__}")
    return tree


def names_in(expr: str) -> Set[str]:
    tree = parse(expr)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in FUNCS:
            out.add(node.id)
    return out


def evaluate(expr: str, env: Dict[str, Any]):
    tree = parse(expr)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise FormulaError(f"unknown name: {node.id}")
            return env[node.id]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -np.asarray(v, dtype=float) if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.Call):
            args = [ev(a) for a in node.args]
            return FUNCS[node.func.id](*args)
        raise FormulaError("unsupported node")

    with np.errstate(all="ignore"):
        return ev(tree)
