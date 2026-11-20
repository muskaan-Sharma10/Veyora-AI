import os
import json
import re
import logging
from typing import Optional
from google import genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for all pipeline agents using Google Gemini API."""

    def __init__(self, name: str, description: str, model_name: Optional[str] = None):
        self.name = name
        self.description = description
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Add a Gemini API key to the project's .env file before starting a review.")

        self.client = genai.Client(api_key=api_key)
        logger.info(f"[{self.name}] Initialized with model {self.model_name}")

    def call_llm(self, prompt: str) -> str:
        """Call the LLM and return raw text response."""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            if not response.text:
                raise RuntimeError("The model returned an empty response.")
            return response.text
        except Exception as e:
            logger.error(f"[{self.name}] LLM call failed: {e}")
            raise

    def call_llm_json(self, prompt: str) -> dict:
        """Call LLM and return parsed JSON."""
        full_prompt = (
            prompt
            + "\n\n--- OUTPUT REQUIREMENT ---"
            + "\nReturn ONLY valid JSON. No markdown code blocks, no explanation text, no comments. Just the raw JSON object."
        )
        try:
            response_text = self.call_llm(full_prompt).strip()

            # Strip markdown code fences if present
            response_text = re.sub(r'^```(?:json)?\s*', '', response_text, flags=re.MULTILINE)
            response_text = re.sub(r'\s*```$', '', response_text, flags=re.MULTILINE)
            response_text = response_text.strip()

            # Attempt direct parse
            try:
                return json.loads(response_text)
            except json.JSONDecodeError:
                pass

            # Try to extract JSON object
            match = re.search(r'\{[\s\S]*\}', response_text)
            if match:
                return json.loads(match.group())

            # Try to extract JSON array
            match = re.search(r'\[[\s\S]*\]', response_text)
            if match:
                return json.loads(match.group())

            raise ValueError(f"Could not parse JSON from response: {response_text[:300]}")

        except Exception as e:
            logger.error(f"[{self.name}] JSON parsing failed: {e}")
            raise

    def run(self, *args, **kwargs):
        raise NotImplementedError(f"Agent '{self.name}' must implement run()")

    def __repr__(self):
        return f"<{self.__class__.__name__} name='{self.name}'>"
