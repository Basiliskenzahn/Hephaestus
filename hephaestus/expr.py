"""Safe arithmetic over named parameters.

Grammar: numbers, parameter names, + - * /, parentheses, unary minus/plus. Nothing else:
no calls, no attributes, no comparisons. Parsed with `ast`, never `eval`.
"""
import ast
import math


class ExprError(ValueError):
    pass


_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


def parse(text):
    """Parse an expression string; returns the AST body. Raises ExprError on anything outside the grammar."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        raise ExprError(f"cannot parse expression '{text}'") from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.Name, ast.BinOp, ast.UnaryOp,
                             ast.USub, ast.UAdd, *_BINOPS)):
            continue
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            continue
        raise ExprError(f"expression '{text}' uses unsupported syntax ({type(node).__name__}); "
                        "allowed: numbers, parameter names, + - * / and parentheses")
    return tree.body


def names(text):
    """Parameter names referenced by an expression."""
    return sorted({n.id for n in ast.walk(parse(text)) if isinstance(n, ast.Name)})


def _eval(node, lookup, text):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return lookup(node.id)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, lookup, text)
        return -v if isinstance(node.op, ast.USub) else v
    a, b = _eval(node.left, lookup, text), _eval(node.right, lookup, text)
    if isinstance(node.op, ast.Div) and b == 0:
        raise ExprError(f"division by zero in '{text}'")
    return _BINOPS[type(node.op)](a, b)


def evaluate(value, params):
    """Evaluate a number or expression string against already-resolved parameter values."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value)

    def lookup(name):
        if name not in params:
            known = ", ".join(sorted(params)) or "none defined"
            raise ExprError(f"unknown parameter '{name}' in '{text}' (known parameters: {known})")
        return params[name]

    result = _eval(parse(text), lookup, text)
    if not math.isfinite(result):
        raise ExprError(f"'{text}' does not evaluate to a finite number")
    return result


def resolve_parameters(raw):
    """Resolve {name: number-or-expression} where parameters may reference each other.

    Returns {name: float}. Unknown names and cycles raise ExprError naming the chain.
    """
    done, stack = {}, []

    def visit(name):
        if name in done:
            return done[name]
        if name in stack:
            cycle = " -> ".join(stack[stack.index(name):] + [name])
            raise ExprError(f"parameter cycle: {cycle}")
        if name not in raw:
            known = ", ".join(sorted(raw)) or "none defined"
            where = f" (used by parameter '{stack[-1]}')" if stack else ""
            raise ExprError(f"unknown parameter '{name}'{where}; known parameters: {known}")
        stack.append(name)
        value = raw[name]
        if isinstance(value, str):
            for dep in names(value):
                visit(dep)
        done[name] = evaluate(value, done)
        stack.pop()
        return done[name]

    for name in sorted(raw):
        visit(name)
    return done
