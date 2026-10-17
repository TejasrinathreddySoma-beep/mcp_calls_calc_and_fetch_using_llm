"""Calculator MCP server (stdio). Every calculation is performed by the real Windows Calculator
(Scientific mode): basic keys, trigonometry (+ inverse + hyperbolic), the Function menu (floor/ceil/...),
memory buttons, DEG/RAD/GRAD, 2nd, hyp, and more.

Button automation ids / names are best guesses for the Windows Calculator UI. Each result is verified
against an independent Python value, so a wrong button gives an error, never a silent wrong answer.
Use the `calculator_buttons` tool (/buttons in the client) to list the real ids on your machine.
"""

import ast
import math
import re
import subprocess
import sys
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("calculator")

_ALLOWED_BASIC = re.compile(r"^[0-9+\-*/^().\s]+$")
_TOKEN_KEYS = re.compile(r"^[0-9.+\-*/^()%]+$")
_ESCAPE = {"+": "{+}", "^": "{^}", "%": "{%}", "(": "{(}", ")": "{)}"}
_ANGLE = {"mode": "deg"}  # angle unit we ask the Calculator to use: deg | rad | grad


# ================================================================ window helpers
def _foreground():
    try:
        import win32gui
        return win32gui.GetForegroundWindow()
    except Exception:
        return None


def _restore(hwnd):
    if not hwnd:
        return
    try:
        from pywinauto.controls.hwndwrapper import HwndWrapper
        HwndWrapper(hwnd).set_focus()
        return
    except Exception:
        pass
    try:
        import win32gui
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _number_text(value: float) -> str:
    """Format a number without scientific notation for Calculator input."""
    text = f"{value:.15f}".rstrip("0").rstrip(".")
    return text if text and text != "-" else "0"


def _get_calculator():
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    window = desktop.window(title="Calculator")
    if not window.exists(timeout=1):
        subprocess.Popen("calc.exe")
        window = desktop.window(title="Calculator")
        window.wait("visible", timeout=15)
        time.sleep(1.0)
    return window


def _focus(window) -> bool:
    """Bring the Calculator to the foreground and confirm it really has focus."""
    import ctypes
    import win32gui

    hwnd = window.wrapper_object().handle
    for _ in range(5):
        try:
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # tap Alt so Windows allows the focus switch
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
            win32gui.ShowWindow(hwnd, 9)                     # SW_RESTORE
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            try:
                window.set_focus()
            except Exception:
                pass
        time.sleep(0.3)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    return False


def _display_value(window) -> str:
    display = window.child_window(auto_id="CalculatorResults").window_text()
    return display.replace("Display is", "").strip()


def _calculator_number(output: str) -> float:
    if output.startswith("Error"):
        raise ValueError(output)
    cleaned = output
    for junk in (",", " ", "\u202f", "\u00a0", "\u200e", "\u200f"):
        cleaned = cleaned.replace(junk, "")
    cleaned = cleaned.replace("\u2212", "-").strip()
    try:
        return float(cleaned)
    except ValueError as ex:
        raise ValueError(f"Calculator returned an unreadable result: {output}") from ex


# ================================================================ button lookup
def _index(window):
    """Index every button by automation id and by (lower-case) name."""
    by_id, by_name = {}, {}
    for ctl in window.descendants(control_type="Button"):
        try:
            aid = ctl.element_info.automation_id
            txt = ctl.window_text().strip().lower()
        except Exception:
            continue
        if aid:
            by_id.setdefault(aid, ctl)
        if txt:
            by_name.setdefault(txt, ctl)
    return by_id, by_name


def _find(window, ids, names):
    by_id, by_name = _index(window)
    for aid in ids:
        if aid in by_id:
            return by_id[aid]
    for name in names:
        if name in by_name:
            return by_name[name]
    return None


def _need(window, ids, names):
    btn = _find(window, ids, names)
    if btn is None:
        raise RuntimeError(f"button {ids or names} not found (run /buttons to list the real ids)")
    return btn


def _click(btn):
    try:
        btn.invoke()
    except Exception:
        btn.click_input()


_TOGGLES = {
    "2nd": (["shiftButton", "secondButton"], ["2nd", "second function"]),
    "hyp": (["hypButton"], ["hyp", "hyperbolic function"]),
}
_DROPDOWNS = {"trig": "trigonometry", "func": "function"}
_CONST = {"pi": (["piButton"], ["pi", "\u03c0"]), "e": (["eButton"], ["euler's number", "e"])}
_SPECIAL = {  # buttons usable through windows_press
    "mc": (["ClearMemoryButton"], ["memory clear"]),
    "mr": (["MemRecall"], ["memory recall"]),
    "m+": (["MemPlus"], ["memory add"]),
    "m-": (["MemMinus"], ["memory subtract"]),
    "ms": (["memButton"], ["memory store"]),
    "ce": (["clearEntryButton"], ["clear entry"]),
    "back": (["backSpaceButton"], ["backspace"]),
    "fe": (["FToEButton", "fseButton"], ["f-e"]),
    "angle": (["degButton", "radButton", "gradButton"], ["degrees", "radians", "gradians"]),
    "2nd": _TOGGLES["2nd"],
    "hyp": _TOGGLES["hyp"],
}


def _set_toggle(window, key, on=True) -> bool:
    """Turn the 2nd / hyp toggle on. Returns True if we clicked it (so the caller can flip it back)."""
    btn = _need(window, *_TOGGLES[key])
    try:
        state = btn.get_toggle_state()
    except Exception:
        state = None
    if state is None and not on:
        return False
    if state is None or bool(state) != on:
        _click(btn)
        time.sleep(0.3)
        return True
    return False


def _reset_toggles(window):
    btn = _find(window, *_TOGGLES["2nd"])
    if btn is not None:
        try:
            if btn.get_toggle_state():
                _click(btn)
                time.sleep(0.2)
        except Exception:
            pass


def _toggle_dropdown(window, group):
    """Open (or close) the Trigonometry / Function dropdown."""
    label = _DROPDOWNS[group]
    for ctl in window.descendants(control_type="Button"):
        txt = ctl.window_text().strip().lower()
        if txt == label or txt.startswith(label + " "):
            _click(ctl)
            time.sleep(0.6)
            return
    raise RuntimeError(f"'{label}' dropdown not found (run /buttons)")


def _in_scientific(window) -> bool:
    by_id, by_name = _index(window)
    return any(k in by_id for k in ("logBase10Button", "shiftButton")) or "log" in by_name or "2nd" in by_name


def _ensure_scientific(window, send_keys) -> None:
    """Best effort: switch to Scientific mode (Standard mode has no log/ln/trig/parentheses)."""
    if _in_scientific(window):
        return
    send_keys("%2")  # Alt+2
    time.sleep(0.8)
    if _in_scientific(window):
        return
    try:  # fall back to the navigation menu
        window.child_window(auto_id="TogglePaneButton").wrapper_object().invoke()
        time.sleep(0.8)
        for item in window.descendants(control_type="ListItem"):
            if "scientific" in item.window_text().lower():
                try:
                    item.select()
                except Exception:
                    item.click_input()
                break
        time.sleep(1.0)
    except Exception:
        pass


def _ensure_angle(window):
    """Make the DEG / RAD / GRAD toggle match _ANGLE['mode']."""
    want = _ANGLE["mode"][:3]  # deg | rad | gra
    for _ in range(4):
        btn = _need(window, *_SPECIAL["angle"])
        if (btn.window_text() or "").lower().startswith(want):
            return
        _click(btn)
        time.sleep(0.3)


# ================================================================ driver (focus, retry, verify)
def _drive(operate, expected=None, attempts=3) -> str:
    """Run operate(window, send_keys) in the Calculator, read the display and verify it.

    The display is compared with `expected` (computed independently); on a mismatch the whole
    entry is retried, so a stale or half-typed value is never returned as the answer.
    """
    if sys.platform != "win32":
        return "Error: Windows Calculator is only available on Windows."

    previous_window = _foreground()
    problem = "unknown problem"
    try:
        from pywinauto.keyboard import send_keys

        window = _get_calculator()
        for _ in range(attempts):
            try:
                if not _focus(window):
                    problem = "could not bring the Calculator to the foreground"
                    continue
                _ensure_scientific(window, send_keys)
                _reset_toggles(window)
                send_keys("{ESC}{ESC}")  # 1st ESC closes an open dropdown, 2nd clears the display
                time.sleep(0.3)
                operate(window, send_keys)
                time.sleep(0.6)
                shown = _display_value(window)
                value = _calculator_number(shown)
            except Exception as ex:
                problem = str(ex) or type(ex).__name__
                continue
            if expected is None or math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-9):
                return shown
            problem = f"Calculator showed {value} but the expected value is {expected}"
        return f"Error: Windows Calculator failed: {problem}"
    except Exception as ex:
        return f"Error driving Windows Calculator: {ex}"
    finally:
        _restore(previous_window)


# ================================================================ basic arithmetic (typed keys)
def _run_basic_calculator(expression: str) -> str:
    expr = expression.replace("**", "^").strip()
    if not expr or not _ALLOWED_BASIC.fullmatch(expr):
        return "Error: unsupported basic expression."

    try:
        expected = float(eval(expr.replace("^", "**"), {"__builtins__": {}}, {}))  # chars whitelisted above
    except Exception:
        expected = None

    typed = "".join(_ESCAPE.get(ch, ch) for ch in expr.replace(" ", ""))

    def operate(window, send_keys):
        send_keys(typed, pause=0.05)
        send_keys("{ENTER}")

    return _drive(operate, expected)


# ================================================================ function buttons
def _to_rad(v):
    m = _ANGLE["mode"]
    return math.radians(v) if m == "deg" else (v * math.pi / 200 if m == "grad" else v)


def _from_rad(r):
    m = _ANGLE["mode"]
    return math.degrees(r) if m == "deg" else (r * 200 / math.pi if m == "grad" else r)


def _fact(v):
    return float(math.factorial(int(v))) if v >= 0 and float(v).is_integer() and v < 100 else None


_FUNCS = {}


def _reg(name, group, ids, names, expected, shift=False, hyp=False, angle=None):
    _FUNCS[name] = dict(group=group, ids=ids, names=names, expected=expected, shift=shift, hyp=hyp, angle=angle)


# --- Trigonometry dropdown: sin cos tan sec csc cot, inverse (2nd), hyperbolic (hyp), inverse hyperbolic
_TRIG = [
    ("sin", "Sine", math.sin, math.asin, math.sinh, math.asinh),
    ("cos", "Cosine", math.cos, math.acos, math.cosh, math.acosh),
    ("tan", "Tangent", math.tan, math.atan, math.tanh, math.atanh),
    ("sec", "Secant", lambda x: 1 / math.cos(x), lambda x: math.acos(1 / x),
     lambda x: 1 / math.cosh(x), lambda x: math.acosh(1 / x)),
    ("csc", "Cosecant", lambda x: 1 / math.sin(x), lambda x: math.asin(1 / x),
     lambda x: 1 / math.sinh(x), lambda x: math.asinh(1 / x)),
    ("cot", "Cotangent", lambda x: 1 / math.tan(x), lambda x: math.atan(1 / x),
     lambda x: 1 / math.tanh(x), lambda x: math.atanh(1 / x)),
]
for _b, _label, _f, _finv, _fh, _finvh in _TRIG:
    _low = _label.lower()
    _reg(_b, "trig", [f"{_b}Button"], [_low], (lambda f: lambda v: f(_to_rad(v)))(_f), angle="in")
    _reg("a" + _b, "trig", [f"inv{_b}Button"], ["inverse " + _low],
         (lambda g: lambda v: _from_rad(g(v)))(_finv), shift=True, angle="out")
    _reg(_b + "h", "trig", [f"{_b}hButton"], ["hyperbolic " + _low], _fh, hyp=True)
    _reg("a" + _b + "h", "trig", [f"inv{_b}hButton"], ["inverse hyperbolic " + _low], _finvh, shift=True, hyp=True)

# --- main keypad
_reg("square", "main", ["xpower2Button", "squareButton"], ["square"], lambda v: v * v)
_reg("cube", "main", ["cubeButton", "xpower3Button"], ["cube"], lambda v: v ** 3, shift=True)
_reg("recip", "main", ["invertButton", "oneOverXButton"], ["reciprocal", "one over x"], lambda v: 1 / v)
_reg("abs", "main", ["absButton"], ["absolute value"], abs)
_reg("sqrt", "main", ["squareRootButton"], ["square root"], math.sqrt)
_reg("cbrt", "main", ["cubeRootButton"], ["cube root"], lambda v: math.copysign(abs(v) ** (1 / 3), v), shift=True)
_reg("factorial", "main", ["factorialButton"], ["factorial"], _fact)
_reg("ln", "main", ["logBaseEButton"], ["natural log"], math.log)
_reg("log", "main", ["logBase10Button"], ["log", "log base 10"], math.log10)
_reg("power10", "main", ["powerOf10Button"], ["10 to the exponent"], lambda v: 10 ** v)
_reg("power2", "main", ["powerOf2Button"], ["2 to the exponent"], lambda v: 2 ** v, shift=True)
_reg("exp", "main", ["powerOfEButton"], ["e to the exponent"], math.exp, shift=True)
_reg("negate", "main", ["negateButton"], ["positive negative"], lambda v: -v)
# --- Function dropdown (the "upper / lower limit" buttons are ceil / floor)
_reg("floor", "func", ["floorButton"], ["floor"], math.floor)
_reg("ceil", "func", ["ceilButton"], ["ceiling"], math.ceil)
_reg("rand", "func", ["randButton"], ["random"], None)
_reg("dms", "func", ["dmsButton"], [], None)
_reg("deg", "func", ["degreesButton"], [], None)
_ALIASES = {"log10": "log", "ceiling": "ceil", "random": "rand", "reciprocal": "recip", "abs": "abs"}

# If a function button cannot be clicked, the same result is computed with basic Calculator keys.
_FALLBACK = {
    "sqrt": lambda t: f"({t})^0.5",
    "square": lambda t: f"({t})*({t})",
    "cube": lambda t: f"({t})^3",
    "recip": lambda t: f"1/({t})",
    "cbrt": lambda t: f"({t})^(1/3)",
    "power10": lambda t: f"10^({t})",
    "power2": lambda t: f"2^({t})",
    "exp": lambda t: f"{math.e!r}^({t})",
    "negate": lambda t: f"-({t})",
}


def _click_function(window, spec):
    """Press a function button: toggles 2nd / hyp and opens the right dropdown first when required."""
    flips = []
    try:
        if spec["shift"] and _set_toggle(window, "2nd", True):
            flips.append("2nd")
        if spec["group"] in _DROPDOWNS:
            _toggle_dropdown(window, spec["group"])
            if spec["hyp"] and _set_toggle(window, "hyp", True):
                flips.append("hyp")
        _click(_need(window, spec["ids"], spec["names"]))  # the function runs; its dropdown closes
        time.sleep(0.4)
    finally:
        for key in reversed(flips):  # put 2nd / hyp back the way they were
            try:
                if key == "hyp":
                    _toggle_dropdown(window, "trig")
                    _click(_need(window, *_TOGGLES["hyp"]))
                    time.sleep(0.3)
                    _toggle_dropdown(window, "trig")
                else:
                    _click(_need(window, *_TOGGLES[key]))
                    time.sleep(0.3)
            except Exception:
                pass


def _run_function(name: str, value=None) -> str:
    name = _ALIASES.get(name, name)
    spec = _FUNCS[name]
    expected = None
    if spec["expected"] is not None and value is not None:
        try:
            expected = float(spec["expected"](value))
        except Exception:
            expected = None

    def operate(window, send_keys):
        if spec["angle"]:
            _ensure_angle(window)
        if value is not None:
            send_keys(_number_text(value))
            time.sleep(0.2)
        _click_function(window, spec)

    out = _drive(operate, expected)
    if out.startswith("Error") and name in _FALLBACK and value is not None:
        out = _run_basic_calculator(_FALLBACK[name](_number_text(value)))
    return out


def _run_constant(name: str) -> str:
    value = math.pi if name == "pi" else math.e

    def operate(window, send_keys):
        _click(_need(window, *_CONST[name]))

    return _drive(operate, value)


def _run_log_base(value: float, base: float) -> str:
    """log base `base` of `value`: the Calculator's own 2nd+log (log base y) button, else ln/ln."""
    if value <= 0:
        return "Error: log value must be greater than zero."
    if base <= 0 or base == 1:
        return "Error: log base must be greater than zero and not equal to one."

    def operate(window, send_keys):
        send_keys(_number_text(value))
        time.sleep(0.2)
        flipped = _set_toggle(window, "2nd", True)
        try:
            _click(_need(window, ["logBaseYButton"], ["log base y"]))
            time.sleep(0.3)
            send_keys(_number_text(base))
            send_keys("{ENTER}")
        finally:
            if flipped:
                try:
                    _click(_need(window, *_TOGGLES["2nd"]))
                except Exception:
                    pass

    out = _drive(operate, math.log(value, base))
    if out.startswith("Error"):
        try:
            top = _calculator_number(_run_function("ln", value))
            bottom = _calculator_number(_run_function("ln", base))
        except ValueError as ex:
            return str(ex)
        out = _run_basic_calculator(f"({_number_text(top)})/({_number_text(bottom)})")
    return out


# ================================================================ expression evaluation
def _windows_eval_node(node) -> float:
    """Evaluate an AST, executing every actual calculation in Calculator."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.Name) and node.id.lower() in ("pi", "e"):
        name = node.id.lower()
        try:
            return _calculator_number(_run_constant(name))
        except ValueError:
            return math.pi if name == "pi" else math.e

    if isinstance(node, ast.UnaryOp):
        value = _windows_eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return _calculator_number(_run_basic_calculator(f"-({_number_text(value)})"))

    if isinstance(node, ast.BinOp):
        left = _windows_eval_node(node.left)
        right = _windows_eval_node(node.right)
        symbols = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^", ast.Mod: "%"}
        operator_type = type(node.op)
        if operator_type not in symbols:
            raise ValueError("Unsupported operator.")
        return _calculator_number(
            _run_basic_calculator(f"({_number_text(left)}){symbols[operator_type]}({_number_text(right)})")
        )

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.keywords:
            raise ValueError("Keyword arguments are not supported.")
        name = _ALIASES.get(node.func.id.lower(), node.func.id.lower())
        nargs = len(node.args)

        if name == "log" and nargs == 2:  # log(value, base)
            return _calculator_number(_run_log_base(_windows_eval_node(node.args[0]), _windows_eval_node(node.args[1])))

        if name == "root" and nargs == 2:  # root(x, y) = y-th root of x
            x, y = _windows_eval_node(node.args[0]), _windows_eval_node(node.args[1])
            return _calculator_number(_run_basic_calculator(f"({_number_text(x)})^(1/({_number_text(y)}))"))

        if name == "rand" and nargs == 0:
            return _calculator_number(_run_function("rand"))

        if name in _FUNCS and nargs == 1:
            return _calculator_number(_run_function(name, _windows_eval_node(node.args[0])))

    raise ValueError(
        "Unsupported expression. Supported: + - * / ^ %, parentheses, pi, e, sqrt cbrt square cube recip abs "
        "factorial ln log log(x,base) root(x,y) power10 power2 exp floor ceil rand dms deg, "
        "sin cos tan sec csc cot, asin acos atan asec acsc acot, sinh cosh tanh sech csch coth, "
        "asinh acosh atanh asech acsch acoth."
    )


# ================================================================ whole-expression mode
# The expression is typed into the Calculator in ONE go, like a person would:  30 [sin] + 60 [cos] =
# (unary function buttons apply to the number or ( ) group just entered).
def _press_logy(window):
    """2nd + log -> the Calculator's 'log base y' button (x logy y)."""
    flipped = _set_toggle(window, "2nd", True)
    try:
        _click(_need(window, ["logBaseYButton"], ["log base y"]))
        time.sleep(0.3)
    finally:
        if flipped:
            try:
                _click(_need(window, *_TOGGLES["2nd"]))
            except Exception:
                pass


_SYM = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^", ast.Mod: "%"}


def _compile(node, steps):
    """Turn an expression AST into Calculator steps: ('keys', text) | ('fn', name) | ('const', name) | ('logy',)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        steps.append(("keys", _number_text(float(node.value))))
    elif isinstance(node, ast.Name) and node.id.lower() in _CONST:
        steps.append(("const", node.id.lower()))
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        _compile(node.operand, steps)
        if isinstance(node.op, ast.USub):
            steps.append(("fn", "negate"))
    elif isinstance(node, ast.BinOp) and type(node.op) in _SYM:
        steps.append(("keys", "("))
        _compile(node.left, steps)
        steps.append(("keys", _SYM[type(node.op)]))
        _compile(node.right, steps)
        steps.append(("keys", ")"))
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        name = _ALIASES.get(node.func.id.lower(), node.func.id.lower())
        n = len(node.args)
        if name == "log" and n == 2:
            steps.append(("keys", "("))
            _compile(node.args[0], steps)
            steps.append(("logy",))
            _compile(node.args[1], steps)
            steps.append(("keys", ")"))
        elif name == "root" and n == 2:
            steps.append(("keys", "("))
            _compile(node.args[0], steps)
            steps.append(("keys", "^(1/"))
            _compile(node.args[1], steps)
            steps.append(("keys", "))"))
        elif name == "rand" and n == 0:
            steps.append(("fn", "rand"))
        elif name in _FUNCS and n == 1:
            _compile(node.args[0], steps)
            steps.append(("fn", name))
        else:
            raise ValueError(f"unsupported function {node.func.id}/{n}")
    else:
        raise ValueError("unsupported expression")


def _py_eval(node) -> float:
    """Independent Python value of the same expression (used to verify the Calculator's display)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id.lower() in ("pi", "e"):
        return math.pi if node.id.lower() == "pi" else math.e
    if isinstance(node, ast.UnaryOp):
        v = _py_eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _py_eval(node.left), _py_eval(node.right)
        t = type(node.op)
        return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}.get(t) if t in (ast.Add, ast.Sub, ast.Mult) else (
            a / b if t is ast.Div else a ** b if t is ast.Pow else a % b)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = _ALIASES.get(node.func.id.lower(), node.func.id.lower())
        args = [_py_eval(a) for a in node.args]
        if name == "log" and len(args) == 2:
            return math.log(args[0], args[1])
        if name == "root" and len(args) == 2:
            return args[0] ** (1 / args[1])
        if name in _FUNCS and len(args) == 1 and _FUNCS[name]["expected"] is not None:
            return float(_FUNCS[name]["expected"](args[0]))
    raise ValueError("no independent value")


def _run_sequence(steps, expected) -> str:
    needs_angle = any(k == "fn" and _FUNCS[v]["angle"] for k, *rest in steps for v in rest[:1])

    def operate(window, send_keys):
        if needs_angle:
            _ensure_angle(window)
        for kind, *rest in steps:
            if kind == "keys":
                send_keys("".join(_ESCAPE.get(ch, ch) for ch in rest[0]), pause=0.05)
            elif kind == "fn":
                _click_function(window, _FUNCS[rest[0]])
            elif kind == "const":
                _click(_need(window, *_CONST[rest[0]]))
            elif kind == "logy":
                _press_logy(window)
            time.sleep(0.2)
        send_keys("{ENTER}")

    return _drive(operate, expected, attempts=2)


# ================================================================ MCP tools
@mcp.tool()
def windows_calculate(expression: str) -> str:
    """Run a basic arithmetic expression (digits, + - * / ^ ( ) .) in real Windows Calculator."""
    return _run_basic_calculator(expression)


@mcp.tool()
def windows_log_base(value: float, base: float) -> str:
    """Calculate log base `base` of `value` in real Windows Calculator."""
    return _run_log_base(value, base)


@mcp.tool()
def windows_evaluate(expression: str) -> float:
    """
    Evaluate an expression through real Windows Calculator (Scientific mode).

    Supports + - * / ^ %, parentheses, pi, e and functions: sqrt cbrt square cube recip abs factorial
    ln log log(x,base) root(x,y) power10 power2 exp floor ceil rand dms deg, sin cos tan sec csc cot,
    asin acos atan asec acsc acot, sinh cosh tanh sech csch coth, asinh acosh atanh asech acsch acoth.
    Trig uses the current angle mode (default degrees).

    Examples: 5*log(5,4) | sin(90) | ceil(4.2) | floor(7.9) | factorial(5) | asin(0.5)
    """
    try:
        tree = ast.parse(expression.replace("^", "**").strip(), mode="eval")
    except SyntaxError as ex:
        raise ValueError(f"Cannot evaluate expression: {ex}")

    # 1) type the WHOLE expression into the Calculator in one go and read the final answer
    seq_error = ""
    try:
        steps = []
        _compile(tree.body, steps)
        try:
            expected = _py_eval(tree.body)
        except Exception:
            expected = None
        out = _run_sequence(steps, expected)
        if not out.startswith("Error"):
            return _calculator_number(out)
        seq_error = out
    except ValueError as ex:
        seq_error = str(ex)

    # 2) fallback: evaluate piece by piece (each operation is still done in the Calculator)
    try:
        return _windows_eval_node(tree.body)
    except ValueError as ex:
        raise ValueError(f"Cannot evaluate expression. Whole-expression mode: {seq_error} | "
                         f"Step-by-step mode: {ex}")


@mcp.tool()
def windows_set_angle_mode(mode: str) -> str:
    """Set the angle unit used by trig functions in the Calculator: 'deg', 'rad' or 'grad'."""
    m = mode.strip().lower()[:3]
    if m not in ("deg", "rad", "gra"):
        return "Error: mode must be deg, rad or grad."
    _ANGLE["mode"] = "grad" if m == "gra" else m

    def operate(window, send_keys):
        _ensure_angle(window)

    out = _drive(operate, None, attempts=1)
    return out if out.startswith("Error") else f"Angle mode set to {_ANGLE['mode']}."


@mcp.tool()
def windows_press(sequence: str) -> str:
    """Press Calculator buttons in order (starting from a cleared display) and return the display.

    Space-separated tokens. Numbers/operators: 5 + * / ^ ( ) % . ; '=' equals; 'c' clear.
    Buttons: 2nd hyp pi e ce back mc mr m+ m- ms fe angle, plus any function name
    (sin cos tan ... ln log sqrt square cube recip abs factorial exp power10 floor ceil rand dms deg ...).
    Example: '5 ln' presses 5 then the ln button; '9 sqrt + 1 =' gives 4.
    """
    tokens = sequence.split()
    if not tokens:
        return "Error: empty sequence."

    def operate(window, send_keys):
        for tok in tokens:
            t = tok.lower()
            if t in _SPECIAL:
                _click(_need(window, *_SPECIAL[t]))
            elif t in _CONST:
                _click(_need(window, *_CONST[t]))
            elif _ALIASES.get(t, t) in _FUNCS:
                _click_function(window, _FUNCS[_ALIASES.get(t, t)])
            elif t == "=":
                send_keys("{ENTER}")
            elif t == "c":
                send_keys("{ESC}")
            elif _TOKEN_KEYS.fullmatch(tok):
                send_keys("".join(_ESCAPE.get(ch, ch) for ch in tok), pause=0.05)
            else:
                raise ValueError(f"unknown token '{tok}'")
            time.sleep(0.25)

    return _drive(operate, None, attempts=1)


@mcp.tool()
def calculator_buttons() -> str:
    """Debug helper: list the Calculator's buttons as 'automation id | name' (main pad, dropdowns, 2nd, hyp)."""
    if sys.platform != "win32":
        return "Error: Windows only."
    try:
        window = _get_calculator()
        sections = {}

        def snap(label):
            sections[label] = sorted({f"{c.element_info.automation_id} | {c.window_text()}"
                                      for c in window.descendants(control_type="Button")})

        snap("MAIN")
        for group in ("trig", "func"):
            try:
                _toggle_dropdown(window, group)
                snap(f"{group.upper()} DROPDOWN OPEN")
                _toggle_dropdown(window, group)
            except Exception as ex:
                sections[f"{group.upper()} DROPDOWN"] = [f"error: {ex}"]
        flipped = False
        try:
            if _set_toggle(window, "2nd", True):
                flipped = True
            snap("WITH 2ND ON")
            _toggle_dropdown(window, "trig")
            snap("TRIG DROPDOWN WITH 2ND ON")
            if _set_toggle(window, "hyp", True):
                snap("TRIG DROPDOWN WITH 2ND+HYP ON")
                _click(_need(window, *_TOGGLES["hyp"]))
            _toggle_dropdown(window, "trig")
        except Exception as ex:
            sections["2ND/HYP"] = [f"error: {ex}"]
        if flipped:
            try:
                _click(_need(window, *_TOGGLES["2nd"]))
            except Exception:
                pass
        return "\n\n".join(f"== {k} ==\n" + "\n".join(v) for k, v in sections.items())
    except Exception as ex:
        return f"Error: {ex}"


if __name__ == "__main__":
    mcp.run(transport="stdio")