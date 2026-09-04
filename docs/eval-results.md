# Eval results (routing only, no Fireworks call)

Source: held-out `fixtures/prompts.json` against the live router `/api/v1/eval`.

Complexity candidates live in `fixtures/complexity-exemplars.json` and
`config.yaml` only. Threshold is `0.75`. Agent/long-context rows use
`tools`, `messages`, or `pad_tokens` — they are not in the slogan bank.

Re-run after serving the current config:

```bash
vllm-sr serve --config config.yaml
python3 scripts/eval_suite.py
```

The suite exits 3 on mis-routes. Publish whatever miss rate you get.
Do not paste eval prompts back into `routing.signals.complexity`.
