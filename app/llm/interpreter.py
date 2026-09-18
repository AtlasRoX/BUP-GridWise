import json
import logging
import re
from typing import List
from openai import APIError
from app.config import settings
from app.llm.client import get_llm_client
from app.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from app.models.directives import DirectiveInterpretation
from app.models.request import BatteryConfig
from app.rules.normalizer import fallback_interpret_note

logger = logging.getLogger("gridwise.llm.interpreter")


class LLMInterpretationError(Exception):
    """Raised when language model interpretation fails or model is unconfigured."""
    pass


def clean_json_response(content: str) -> str:
    """
    Strips markdown code blocks, backticks, or trailing characters.
    """
    content = content.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
    if match:
        content = match.group(1).strip()
    return content


async def interpret_operator_notes(
    operator_notes: List[str], battery: BatteryConfig
) -> List[DirectiveInterpretation]:
    """
    Interprets 1-3 operator notes using NVIDIA NIM Nemotron model.
    In accordance with BUP CSE Fest competition rules, an LLM interpretation step
    is strictly mandatory. If the provider is unavailable or fails after retry,
    a controlled LLMInterpretationError is raised.
    """
    api_key = settings.effective_api_key

    if not api_key:
        logger.error("NVIDIA NIM API key is not configured. Language-model interpretation is mandatory.")
        raise LLMInterpretationError(
            "NVIDIA NIM API key is unconfigured. Competition rules strictly require language-model interpretation."
        )

    client = get_llm_client()
    user_prompt = build_user_prompt(operator_notes, battery)

    last_error: Exception | None = None

    # Attempt inference with 1 retry on transient failure (bounded <=12.5s total budget)
    for attempt in range(2):
        try:
            logger.info(
                f"Calling NVIDIA NIM ({settings.effective_model}) for {len(operator_notes)} notes (attempt {attempt + 1})"
            )
            response = await client.chat.completions.create(
                model=settings.effective_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=1500,
                timeout=settings.llm_timeout_seconds,
            )

            raw_text = response.choices[0].message.content or ""
            clean_text = clean_json_response(raw_text)
            parsed_data = json.loads(clean_text)

            if isinstance(parsed_data, dict) and "directive_interpretation" in parsed_data:
                parsed_data = parsed_data["directive_interpretation"]
            elif isinstance(parsed_data, dict) and "interpretations" in parsed_data:
                parsed_data = parsed_data["interpretations"]

            if not isinstance(parsed_data, list):
                raise ValueError("Expected JSON array of directive interpretations")

            interpretations: List[DirectiveInterpretation] = []
            for item in parsed_data:
                if isinstance(item, dict) and not item.get("explanation"):
                    item["explanation"] = f"Directive {item.get('directive_type', 'interpreted')} applied."
                interp = DirectiveInterpretation.model_validate(item)
                interpretations.append(interp)

            # Check that we received interpretations matching all notes
            if len(interpretations) == len(operator_notes):
                # Ensure note_index alignment
                interpretations = sorted(interpretations, key=lambda x: x.note_index)
                return interpretations
            else:
                raise ValueError(
                    f"LLM returned {len(interpretations)} items, expected {len(operator_notes)}"
                )

        except Exception as e:
            last_error = e
            logger.warning(f"NVIDIA NIM attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                import asyncio
                await asyncio.sleep(0.5)

    # Provider failed after bounded retry
    logger.error(f"NVIDIA NIM interpretation failed after retries: {last_error}")
    raise LLMInterpretationError(
        f"Language model interpretation failed after bounded retry: {last_error}"
    )
