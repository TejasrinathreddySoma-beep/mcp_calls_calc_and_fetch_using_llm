"""Chat client: local Ollama LLM + Windows Calculator MCP server."""

import asyncio
import json
import re
import sys

import ollama
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


MODEL = "llama3.1:8b"

EXTRACT_SYSTEM = (
    "Decide whether the user's message asks for a calculation. Return JSON only. "
    "If it is a calculation, set is_calculation to true and convert it into one "
    "valid expression. Never solve the calculation yourself. "
    "Supported expression syntax: +, -, *, /, ^, parentheses, pi, e, sqrt(x), "
    "log(x), log(value,base), log10(x), ln(x), sin(x), cos(x), tan(x), "
    "asin(x), acos(x), atan(x), sinh(x), cosh(x), tanh(x), "
    "asinh(x), acosh(x), atanh(x), exp(x), power10(x), factorial(x), "
    "square(x), cube(x), floor(x), ceil(x). "
    "For 'log 5 base 4' or 'LOG5 BASE 4', use log(5,4). "
    "For '94 times log 5 base 7', use 94*log(5,7). "
    "For 'sin 90', use sin(90). "
    "For 'square root of 81', use sqrt(81). "
    "For ordinary chat with no calculation, set is_calculation to false and "
    'expression to "".'
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
    r"\b(?:plus|minus|times|multiply|divide|divided|over|power|percent|"
    r"sqrt|square|cube|log|ln|sin|cos|tan|factorial|floor|ceil)\b|"
    r"[+\-*/^()]",
    re.IGNORECASE,
)


async def run_tool(session, name, args):
    try:
        result = await session.call_tool(name, args)

        text = "\n".join(
            content.text
            for content in result.content
            if content.type == "text"
        )

        return f"Error: {text}" if result.isError else text

    except Exception as ex:
        return f"Error: {ex}"


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=["calc_server.py"],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            tool_names = ", ".join(tool.name for tool in tools)

            print(f"Connected to MCP calculator. Tools: {tool_names}")
            print(f"Model: {MODEL}")
            print("Examples: sin 90 | 5 times log 5 base 4 | square root of 81")
            print("Direct expression: /calc 5*log(5,4) | Quit: exit")

            llm = ollama.AsyncClient()

            chat_history = [
                {
                    "role": "system",
                    "content": (
                        "You are a friendly assistant. Keep replies short. "
                        "Do not perform calculations. The calculator handles calculations."
                    ),
                }
            ]

            while True:
                q = (await asyncio.to_thread(input, "\nyou> ")).strip()

                if not q:
                    continue

                if q.lower() in {"exit", "quit", "q"}:
                    break

                # Direct mathematical expression. This still uses Windows Calculator.
                if q.lower().startswith("/calc "):
                    expression = q[6:].strip()

                    out = await run_tool(
                        session,
                        "windows_evaluate",
                        {"expression": expression},
                    )

                    if out.startswith("Error"):
                        print(f"calc> I couldn't calculate that: {out}")
                    else:
                        print(f"calc> The result is {out}.")

                    continue

                # Ask Ollama only to translate English to an expression.
                extraction_response = await llm.chat(
                    model=MODEL,
                    format=SCHEMA,
                    options={"temperature": 0},
                    messages=[
                        {"role": "system", "content": EXTRACT_SYSTEM},
                        {"role": "user", "content": q},
                    ],
                )

                try:
                    plan = json.loads(extraction_response.message.content)
                except (json.JSONDecodeError, TypeError):
                    plan = {"is_calculation": False, "expression": ""}

                is_calculation = bool(plan.get("is_calculation"))
                expression = str(plan.get("expression", "")).strip()

                if is_calculation and expression:
                    print(f"  [llm] expression: {expression}")

                    # Windows Calculator performs the calculation.
                    out = await run_tool(
                        session,
                        "windows_evaluate",
                        {"expression": expression},
                    )

                    print(f"  [tool] windows_evaluate -> {out}")

                    if out.startswith("Error"):
                        print(f"calc> I couldn't calculate that: {out}")
                    else:
                        print(f"calc> The result is {out}.")

                    # Never ask Ollama to answer a calculation.
                    continue

                # If the message looks mathematical but Ollama did not translate it,
                # refuse rather than allowing it to make up an answer.
                if MATH_HINT.search(q):
                    print(
                        "calc> I could not convert that into a supported "
                        "Calculator expression. Try a clearer format."
                    )
                    continue

                # Normal non-mathematical chat is allowed.
                chat_history.append({"role": "user", "content": q})

                chat_response = await llm.chat(
                    model=MODEL,
                    messages=chat_history,
                )

                chat_history.append(
                    {
                        "role": "assistant",
                        "content": chat_response.message.content,
                    }
                )

                print(f"llm> {chat_response.message.content}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass