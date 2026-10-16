"""Calculator MCP server (stdio). Uses the real Windows Calculator."""

import ast
import math
import re
import subprocess
import sys
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("calculator")

_ALLOWED_BASIC = re.compile(r"^[0-9+\-*/^().\s]+$")
_ESCAPE = {
    "+": "{+}",
    "^": "{^}",
    "%": "{%}",
    "(": "{(}",
    ")": "{)}",
}


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
    return text if text else "0"


def _get_calculator():
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    window = desktop.window(title="Calculator")

    if not window.exists(timeout=1):
        subprocess.Popen("calc.exe")
        window = desktop.window(title="Calculator")
        window.wait("visible", timeout=15)

    return window


def _display_value(window) -> str:
    display = window.child_window(
        auto_id="CalculatorResults"
    ).window_text()

    return display.replace("Display is", "").strip()


def _calculator_number(output: str) -> float:
    if output.startswith("Error"):
        raise ValueError(output)

    cleaned = output.replace(",", "").replace("−", "-").strip()

    try:
        return float(cleaned)
    except ValueError as ex:
        raise ValueError(f"Calculator returned an unreadable result: {output}") from ex


def _run_basic_calculator(expression: str) -> str:
    """Type a basic expression into real Windows Calculator."""
    if sys.platform != "win32":
        return "Error: Windows Calculator is only available on Windows."

    expr = expression.replace("**", "^").strip()

    if not expr or not _ALLOWED_BASIC.fullmatch(expr):
        return "Error: unsupported basic expression."

    previous_window = _foreground()

    try:
        from pywinauto.keyboard import send_keys

        window = _get_calculator()
        window.set_focus()

        send_keys("%2")       # Alt+2: Scientific mode
        time.sleep(0.25)
        send_keys("{ESC}")    # Clear
        time.sleep(0.15)

        typed = "".join(_ESCAPE.get(char, char) for char in expr.replace(" ", ""))
        send_keys(typed, pause=0.03)
        send_keys("{ENTER}")
        time.sleep(0.35)

        return _display_value(window)

    except Exception as ex:
        return f"Error driving Windows Calculator: {ex}"

    finally:
        _restore(previous_window)


def _run_log_base(value: float, base: float) -> str:
    """Calculate log base `base` of `value` with Windows Calculator."""
    if value <= 0:
        return "Error: log value must be greater than zero."

    if base <= 0 or base == 1:
        return "Error: log base must be greater than zero and not equal to one."

    previous_window = _foreground()

    try:
        from pywinauto.keyboard import send_keys

        window = _get_calculator()
        window.set_focus()

        send_keys("%2")       # Scientific mode
        time.sleep(0.25)
        send_keys("{ESC}")

        # Calculator's log_y(x): type x, Shift+L, then y.
        send_keys(_number_text(value))
        send_keys("+l")       # Shift+L: log base y of x
        send_keys(_number_text(base))
        send_keys("{ENTER}")
        time.sleep(0.35)

        return _display_value(window)

    except Exception as ex:
        return f"Error driving Windows Calculator: {ex}"

    finally:
        _restore(previous_window)


# Windows Calculator Scientific-mode shortcut keys.

_UNARY_SCIENTIFIC_BUTTONS = {
    "sin": "sinButton",
    "cos": "cosButton",
    "tan": "tanButton",

    "asin": "invsinButton",
    "acos": "invcosButton",
    "atan": "invtanButton",

    "sinh": "sinhButton",
    "cosh": "coshButton",
    "tanh": "tanhButton",

    "asinh": "invsinhButton",
    "acosh": "invcoshButton",
    "atanh": "invtanhButton",

    "log": "logBase10Button",
    "log10": "logBase10Button",
    "ln": "logBaseEButton",

    "exp": "expButton",
    "power10": "powerOf10Button",

    "square": "xpower2Button",
    "cube": "xpower3Button",
    "factorial": "factorialButton",
}


def _run_unary_calculator(value: float, button_id: str) -> str:
    """Run a Scientific Calculator unary function by clicking its UI button."""
    previous_window = _foreground()

    try:
        from pywinauto.keyboard import send_keys

        window = _get_calculator()
        window.set_focus()

        send_keys("%2")       # Scientific mode
        time.sleep(0.4)
        send_keys("{ESC}")    # Clear

        send_keys(_number_text(value))
        time.sleep(0.2)

        button = window.child_window(
            auto_id=button_id,
            control_type="Button",
        )
        button.wait("enabled", timeout=5)
        button.click_input()

        time.sleep(0.5)
        return _display_value(window)

    except Exception as ex:
        return f"Error driving Windows Calculator: {ex}"

    finally:
        _restore(previous_window)


def _windows_eval_node(node) -> float:
    """Evaluate an AST, executing every actual calculation in Calculator."""

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.Name):
        constants = {
            "pi": math.pi,
            "e": math.e,
        }

        name = node.id.lower()

        if name in constants:
            return constants[name]

    if isinstance(node, ast.UnaryOp):
        value = _windows_eval_node(node.operand)

        if isinstance(node.op, ast.UAdd):
            return value

        if isinstance(node.op, ast.USub):
            return _calculator_number(
                _run_basic_calculator(f"-({_number_text(value)})")
            )

    if isinstance(node, ast.BinOp):
        left = _windows_eval_node(node.left)
        right = _windows_eval_node(node.right)

        symbols = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.Pow: "^",
            ast.Mod: "%",
            ast.FloorDiv: "/",
        }

        operator_type = type(node.op)

        if operator_type not in symbols:
            raise ValueError("Unsupported operator.")

        return _calculator_number(
            _run_basic_calculator(
                f"({_number_text(left)}){symbols[operator_type]}({_number_text(right)})"
            )
        )

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.keywords:
            raise ValueError("Keyword arguments are not supported.")

        function_name = node.func.id.lower()

        # log(value, base): for example log(5, 4) means log base 4 of 5.
        if function_name == "log" and len(node.args) == 2:
            value = _windows_eval_node(node.args[0])
            base = _windows_eval_node(node.args[1])

            return _calculator_number(_run_log_base(value, base))

        # log(x) means base-10 log in this calculator.
        if function_name == "log" and len(node.args) == 1:
            value = _windows_eval_node(node.args[0])
            return _calculator_number(_run_unary_calculator(value, "l"))

        # sqrt(x) is performed with Calculator's exponent operation.
        if function_name == "sqrt" and len(node.args) == 1:
            value = _windows_eval_node(node.args[0])

            return _calculator_number(
                _run_basic_calculator(f"({_number_text(value)})^0.5")
            )

        if function_name in _UNARY_SCIENTIFIC_BUTTONS and len(node.args) == 1:
            value = _windows_eval_node(node.args[0])

            return _calculator_number(
                _run_unary_calculator(value, _UNARY_SCIENTIFIC_BUTTONS[function_name])
            )

    raise ValueError(
        "Unsupported expression. Supported: + - * / ^, parentheses, pi, e, "
        "sqrt(x), log(x), log(value,base), log10(x), ln(x), "
        "sin/cos/tan, inverse and hyperbolic trig, exp(x), power10(x), "
        "square(x), cube(x), factorial(x), floor(x), ceil(x)."
    )


@mcp.tool()
def windows_calculate(expression: str) -> str:
    """Run a basic arithmetic expression in real Windows Calculator."""
    return _run_basic_calculator(expression)


@mcp.tool()
def windows_log_base(value: float, base: float) -> str:
    """Calculate log base `base` of `value` in real Windows Calculator."""
    return _run_log_base(value, base)


@mcp.tool()
def windows_evaluate(expression: str) -> float:
    """
    Evaluate an expression through real Windows Calculator.

    Examples:
    5*log(5,4)
    sin(90)
    sqrt(81)+3^2
    factorial(5)
    """
    normalized = expression.replace("^", "**")

    try:
        tree = ast.parse(normalized, mode="eval")
        return _windows_eval_node(tree.body)
    except (SyntaxError, ValueError) as ex:
        raise ValueError(f"Cannot evaluate expression: {ex}")


if __name__ == "__main__":
    mcp.run(transport="stdio")