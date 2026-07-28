"""Tool-Call Rewrite Evaluation harness for AuroraCoder.

This package does NOT modify any AuroraCoder production/dev-branch source file.
It only *reads* AuroraCoder internals (the RangeReplaceEditor from
src/code_tools/edit_file.py) via an isolated importlib load so the heavy package
chain (google_search / web_browser / subagent / tool_store) is never triggered.

All trial artifacts live under this package's own `fixtures/` directory, which is
populated by COPYING real repo files (never by mutating originals).
"""