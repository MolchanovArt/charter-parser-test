from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


class OpenAIResponsesClient:
    def __init__(self, model: str = "gpt-5.4", store: bool = False):
        self.client = OpenAI()
        self.model = model
        self.store = store

    def json_response(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
        reasoning_effort: str = "low",
    ) -> dict[str, Any]:
        response = self.client.responses.create(
            model=self.model,
            reasoning={"effort": reasoning_effort},
            store=self.store,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}]},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        return json.loads(response.output_text)
