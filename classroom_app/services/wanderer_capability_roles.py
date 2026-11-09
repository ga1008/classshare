"""Read existing route role declarations; never grant a resource permission."""
import ast
import inspect
import textwrap


def _role_access(node):
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "role"
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "role")


def _explicit_teacher_guard(endpoint, visited=None):
    visited = set() if visited is None else visited
    if endpoint in visited:
        return False
    visited.add(endpoint)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    except (TypeError, ValueError, OSError, IndentationError, SyntaxError):
        return False
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not definitions:
        return False
    # Only unconditional entry guards are role metadata. A nested teacher
    # branch may protect one optional operation in an otherwise student route.
    for node in definitions[0].body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            test = node.test
            if (len(test.ops) == 1 and isinstance(test.ops[0], ast.NotEq) and _role_access(test.left)
                    and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value == "teacher"
                    and any(isinstance(item, ast.Raise) for statement in node.body for item in ast.walk(statement))):
                return True
        call = node.value if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in {"_require_teacher", "require_teacher", "_ensure_teacher"}:
            helper = getattr(endpoint, "__globals__", {}).get(call.func.id)
            if callable(helper) and _explicit_teacher_guard(helper, visited):
                return True
    return False


def student_role_exclusion(route):
    def teacher_dependency(dependant):
        call = getattr(dependant, "call", None)
        return (getattr(call, "__module__", "") == "classroom_app.dependencies"
                and getattr(call, "__name__", "") == "get_current_teacher") or any(
                    teacher_dependency(child) for child in getattr(dependant, "dependencies", []))
    dependant = getattr(route, "dependant", None)
    if (dependant and teacher_dependency(dependant)) or _explicit_teacher_guard(getattr(route, "endpoint", None)):
        return "ordinary_route_requires_teacher"
    return None
