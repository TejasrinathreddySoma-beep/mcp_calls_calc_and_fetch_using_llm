"""Chat client: local Ollama LLM + Windows Calculator MCP server."""

import asyncio
import json
import re
import sys

import ollama
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


MODEL = "qwen2.5:7b"

EXTRACT_SYSTEM = (
    "Decide whether the user's message asks for a calculation. Return JSON only. "
    "If it is a calculation, set is_calculation to true and convert it into one valid expression. "
    "Never solve the calculation yourself. "
    "Supported syntax: + - * / ^ % (mod), parentheses, pi, e, and these functions: "
    "sqrt(x) cbrt(x) square(x) cube(x) recip(x) abs(x) factorial(x) ln(x) log(x) [base 10] "
    "log(x,base) root(x,y) [y-th root of x] power10(x) [10^x] power2(x) [2^x] exp(x) [e^x] "
    "floor(x) ceil(x) rand() dms(x) deg(x) "
    "sin cos tan sec csc cot, asin acos atan asec acsc acot [inverse], "
    "sinh cosh tanh sech csch coth [hyperbolic], asinh acosh atanh asech acsch acoth. "
    "Examples: 'log 5 base 4' -> log(5,4) ; '94 times log 5 base 7' -> 94*log(5,7) ; "
    "'sin 90' -> sin(90) ; 'inverse sine of 0.5' -> asin(0.5) ; 'hyperbolic cosine of 2' -> cosh(2) ; "
    "'square root of 81' -> sqrt(81) ; 'cube root of 27' -> cbrt(27) ; '5 factorial' -> factorial(5) ; "
    "'ceiling of 4.2' or 'upper limit of 4.2' -> ceil(4.2) ; "
    "'floor of 7.9' or 'lower limit of 7.9' -> floor(7.9) ; 'reciprocal of 8' -> recip(8) ; "
    "'17 mod 5' -> 17%5 ; 'e to the power 2' -> exp(2) ; '10 to the power 3' -> power10(3) ; "
    "'absolute value of -6' -> abs(-6) ; 'random number' -> rand(). "
    "For ordinary chat with no calculation, set is_calculation to false and expression to \"\"."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "is_calculation": {"type": "boolean"},
        "expression": {"type": "string"},
    },
    "required": ["is_calculation", "expression"],
}

# If Ollama fails to identify probable maths, do not allow it to invent an answer.
MATH_HINT = re.compile(
    r"\d|"
    r"\b(?:plus|minus|times|multiply|divide|divided|over|power|percent|mod|root|"
    r"sqrt|square|cube|log|ln|sin|cos|tan|factorial|floor|ceil|ceiling|limit|reciprocal|absolute)\b|"
    r"[+\-*/^()]",
    re.IGNORECASE,
)

HELP = """Examples: sin 90 | 5 times log 5 base 4 | ceiling of 4.2 | lower limit of 7.9 | 5 factorial
Commands:
  /calc <expr>      evaluate directly, e.g. /calc 5*log(5,4)
  /press <tokens>   press Calculator buttons, e.g. /press 5 ln   or   /press 9 sqrt + 1 =
                    buttons: 2nd hyp pi e ce back mc mr m+ m- ms fe angle + any function name
  /angle deg|rad|grad   set the angle unit for trig
  /buttons          list the Calculator's real button ids (debug)
  exit              quit"""


async def run_tool(session, name, args):
    try:
        result = await session.call_tool(name, args)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        return f"Error: {text}" if result.isError else text
    except Exception as ex:
        return f"Error: {ex}"


def show(out):
    print(f"calc> I couldn't calculate that: {out}" if out.startswith("Error")
          else f"calc> The result is {out}.")


async def main():
    params = StdioServerParameters(command=sys.executable, args=["calc_server.py"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            print(f"Connected to MCP calculator. Tools: {', '.join(t.name for t in tools)}")
            print(f"Model: {MODEL}")
            print(HELP)

            llm = ollama.AsyncClient()
            chat_history = [{
                "role": "system",
                "content": ("You are a friendly assistant. Keep replies short. "
                            "Do not perform calculations. The calculator handles calculations."),
            }]

            while True:
                q = (await asyncio.to_thread(input, "\nyou> ")).strip()
                if not q:
                    continue
                low = q.lower()
                if low in {"exit", "quit", "q"}:
                    break
                if low in {"help", "/help"}:
                    print(HELP)
                    continue
                if low == "/buttons":
                    print(await run_tool(session, "calculator_buttons", {}))
                    continue
                if low.startswith("/angle "):
                    print("calc>", await run_tool(session, "windows_set_angle_mode", {"mode": q[7:].strip()}))
                    continue
                if low.startswith("/press "):
                    out = await run_tool(session, "windows_press", {"sequence": q[7:].strip()})
                    print(f"calc> display: {out}" if not out.startswith("Error") else f"calc> {out}")
                    continue
                if low.startswith("/calc "):
                    show(await run_tool(session, "windows_evaluate", {"expression": q[6:].strip()}))
                    continue

                # Ollama only translates English into an expression.
                extraction = await llm.chat(
                    model=MODEL, format=SCHEMA, options={"temperature": 0},
                    messages=[{"role": "system", "content": EXTRACT_SYSTEM},
                              {"role": "user", "content": q}],
                )
                try:
                    plan = json.loads(extraction.message.content)
                except (json.JSONDecodeError, TypeError):
                    plan = {"is_calculation": False, "expression": ""}

                expression = str(plan.get("expression", "")).strip()

                if plan.get("is_calculation") and expression:
                    print(f"  [llm] expression: {expression}")
                    out = await run_tool(session, "windows_evaluate", {"expression": expression})
                    print(f"  [tool] windows_evaluate -> {out}")
                    show(out)
                    continue  # never let the LLM answer a calculation

                if MATH_HINT.search(q):
                    print("calc> I could not convert that into a supported Calculator expression. "
                          "Try a clearer format.")
                    continue

                chat_history.append({"role": "user", "content": q})
                reply = await llm.chat(model=MODEL, messages=chat_history)
                chat_history.append({"role": "assistant", "content": reply.message.content})
                print(f"llm> {reply.message.content}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass