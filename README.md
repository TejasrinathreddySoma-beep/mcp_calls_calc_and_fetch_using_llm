# MCP Calculator + Local LLM

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
ollama pull llama3.1:8b        # or qwen2.5:7b (edit MODEL in client.py)
```

## Run
```bash
ollama serve                   # if not already running
python client.py
```
The client launches `calc_server.py` itself over stdio, so you only run one command.

Try: `what is (125 * 48) / 7 + 12?`
The real Windows Calculator opens (in Scientific mode), the expression is typed in, and the displayed result is sent back to the LLM, which replies with the answer.

Notes: the Calculator window title must be `Calculator` (English Windows); don't touch the keyboard while it types.

## Windows Calculator automation
The `windows_calculate` tool opens the real Calculator app, switches it to Scientific mode,
types the expression and reads the result, so you see Calculator working when you ask the LLM.
- Run `python client.py` from a normal desktop session (not SSH/service), and don't touch the keyboard while it types.
- Needs English-titled "Calculator". If your Windows language differs, change `title="Calculator"` in `calc_server.py`.
- If automation fails, the LLM falls back to the `evaluate` tool.

## Debug the server alone
```bash
npx @modelcontextprotocol/inspector python calc_server.py
```
