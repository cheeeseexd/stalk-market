# Opens an interactive Codex chat with GPT-6 Astra that starts by reading the full hand-off (HANDOFF_ASTRA.md).
# Type follow-up prompts straight into this chat. Workspace-write sandbox: it can edit the project but nothing outside it.
codex -m gpt-6-astra -c model_reasoning_effort=high -s workspace-write -C $PSScriptRoot "Read HANDOFF_ASTRA.md in this folder and follow it exactly: it is your full hand-off from Claude. Start with its first instruction."
