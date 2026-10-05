# Epsilon v2 development

```bash
python -m pytest tests/ -q -p no:cacheprovider   # unit/integration/e2e/adversarial
python bench/mbpp_mini.py                        # legacy parity: 16/20 exec
python bench/capability.py                       # 10 capability levels
python -m epsilon bench --quick                  # via CLI
```

- Stdlib only (+ pytest for the suite). Python ≥ 3.11.
- Deterministic: sorted traversal, no randomness, no wall-clock in output.
- Behavior tests, not string tests: assert runtime values, verdict states,
  and round-trips.
- Phone/Termux: keep jobs serial and light (~1 GiB RAM); heavy verification
  belongs to GitHub Actions (public repo = free minutes).
- CI (`.github/workflows/ci.yml`): pytest + mbpp_mini + capability bench.
