"""
Memory Ops — Layer 2: async, post-session distillation.

- ``extractor``  (Layer 2a): structured-output-only pass over a finished
  transcript, behind a strict no-op gate. No tool access, no sandbox —
  safe to run directly in the gateway process.
- ``consolidator`` (Layer 2a): dedupe + usage-decay housekeeping pass.
- ``dispatcher`` (Layer 2b, scaffolding only, disabled by default): the
  tool-using tier (Gap Engine investigation) that needs an isolated,
  on-demand AuroraCoder worker container — see docs/code-agent-memory-design.md.
"""
