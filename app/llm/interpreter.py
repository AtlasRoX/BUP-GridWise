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
    Falls back to deterministic normalizer if API key is unconfigured or provider is unreachable.
    """
    api_key = settings.effective_api_key

    # If no valid API key is present, use the deterministic fallback immediately
    if not api_key or api_key.startswith("dummy-"):
        logger.warning("No NVIDIA NIM API key configured. Utilizing deterministic semantic fallback.")
        return [
            fallback_interpret_note(i, note, battery)
            for i, note in enumerate(operator_notes)
        ]

    client = get_llm_client()
    user_prompt = build_user_prompt(operator_notes, battery)

    # Attempt call with 1 retry on transient failure
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
                logger.warning(
                    f"LLM returned {len(interpretations)} items, expected {len(operator_notes)}. Falling back."
                )

        except Exception as e:
            logger.warning(f"NVIDIA NIM attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                import asyncio
                await asyncio.sleep(1.5)

    # Fallback if both attempts failed
    logger.error("NVIDIA NIM interpretation failed. Engaging deterministic fallback parser.")
    return [
        fallback_interpret_note(i, note, battery)
        for i, note in enumerate(operator_notes)
    ]
