---
# Keep the ordered workflow fallback explicit instead of relying on a single
# mutable model behind the Neurodesk gateway alias.
models:
  neurodesk:
    - openai/glm-5.2
    - openai/kimi-k2.7
    - openai/minimax-m2
---
