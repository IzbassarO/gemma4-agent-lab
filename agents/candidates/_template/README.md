# Candidate template (non-competitive)

A safe directory skeleton for future candidates. It deliberately contains **no tuned prompt, no thinking
configuration and no multi-agent structure**. `README.md` is never packaged: the validator reports it as `NOT_PACKAGED`.

```text
_template/
├── agent.yaml          # single LlmAgent, competition model, all 9 tools, same-directory include
├── prompts/system.md   # placeholder only
└── README.md           # excluded from the ZIP
```

## Using it

```bash
cp -R agents/candidates/_template agents/candidates/<candidate-id>
# replace every TEMPLATE_PLACEHOLDER, record the experiment in experiments/registry.jsonl
uv run python -m tools.validate_submission agents/candidates/<candidate-id>
uv run python -m tools.build_submission agents/candidates/<candidate-id> --experiment-id EXP-YYYYMMDD-NNN --require-clean
```

The template itself fails validation with `PLACEHOLDER_PRESENT` on purpose, so it cannot be packaged by accident.

## Deliberate omissions and what is uncertified

| Omitted / chosen | Why | Blocking H-IDs |
|---|---|---|
| `eval_config.yaml` | Budgets must come from measured runtimes, not guesses. Without it, scorer defaults apply (README documents 60 min / 100 calls / 500 turns / 300 s; not runtime-verified) | H13, H14, H22, H29 |
| `generate_content_config` (sampling, thinking) | `include_thoughts` / `thinking_budget` semantics and forwarding are unresolved | H01, H02, H03 |
| Sub-agents / `agent_tool` | AgentTool history behavior and compaction are uncertified; nested files raise include-base questions | H20, H15, H19, H26 |
| `../` includes | README says blocked, official sample uses them | H26 |
| All 9 tools declared | Undeclared-but-advertised tool calls may be fatal | H04, H18, H30 |
| `adapters/` | LoRA deferred | H16, H24, H25 |
| Anything | Harness version on the scorer is unknown, so compatibility is `CURRENT_HARNESS_COMPATIBILITY_UNKNOWN` | H23 |
