from __future__ import annotations

from time import perf_counter

from .llm_client import extract_json


class MLXJSONClient:
    """与 API 客户端保持同一 chat_json 接口，便于复用完全相同的评测器。"""

    def __init__(
        self,
        model_path: str,
        adapter_path: str | None = None,
        adapter_weights: str | None = None,
        max_tokens: int = 256,
    ) -> None:
        from mlx_lm import load

        self.model, self.tokenizer = load(model_path, adapter_path=adapter_path)
        if adapter_weights is not None:
            import mlx.core as mx

            self.model.load_weights(adapter_weights, strict=False)
            mx.eval(self.model.parameters())
        self.max_tokens = max_tokens
        self.usage: dict[str, int | float] = {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "api_seconds": 0.0,
        }

    def chat_json(self, system: str, user: str) -> dict:
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        started = perf_counter()
        content = generate(
            self.model,
            self.tokenizer,
            prompt=prompt,
            max_tokens=self.max_tokens,
            sampler=make_sampler(temp=0.0),
            verbose=False,
        )
        elapsed = perf_counter() - started
        prompt_tokens = len(self.tokenizer.encode(prompt))
        completion_tokens = len(self.tokenizer.encode(content))
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += prompt_tokens
        self.usage["completion_tokens"] += completion_tokens
        self.usage["total_tokens"] += prompt_tokens + completion_tokens
        self.usage["api_seconds"] += elapsed
        return extract_json(content)
