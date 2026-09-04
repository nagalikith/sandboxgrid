# Eval results (routing only, no Fireworks call)

**Not run.** This box was not serving `config.yaml` from this repo (`vllm-sr` was not installed; nothing listened on the eval port).

Do not backfill a miss table from another process or an older GLM-mid config.

After:

```bash
vllm-sr serve --config config.yaml
python3 scripts/eval_suite.py
```

commit the table as-is. The suite exits 3 on mis-routes. That is the signed result.
