# Director Evals

Offline evals live in `evaluation/`.

Run:

```bash
python3 evaluation/run_evals.py
```

The eval set checks 20 director decisions, including ambiguous briefs, I2V identity preservation, R2V role assignment, Product Truth conflicts, action splitting, prompt budget, fail-closed identity assets, continuity dependencies, single-variable retakes, real-shoot recommendation, and Buffing Wheel mechanical constraints.

Eval cases are schema-checked and do not call paid providers.
