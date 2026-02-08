"""Unified LLM client supporting multiple providers."""

import json
from typing import List, Literal, Optional

from openai import OpenAI
from anthropic import Anthropic
from groq import Groq

from app.config import get_settings

settings = get_settings()


class UnifiedLLMClient:
    """Unified client for multiple LLM providers."""

    def __init__(self):
        """Initialize clients based on configuration."""
        self.provider = settings.llm_provider
        
        # Initialize OpenAI client
        if settings.openai_api_key:
            self.openai_client = OpenAI(api_key=settings.openai_api_key)
        else:
            self.openai_client = None

        # Initialize Anthropic client
        if settings.anthropic_api_key and settings.anthropic_api_key != "your_anthropic_api_key_here":
            self.anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
        else:
            self.anthropic_client = None

        # Initialize Groq client
        if settings.groq_api_key and settings.groq_api_key != "your_groq_api_key_here":
            self.groq_client = Groq(api_key=settings.groq_api_key)
        else:
            self.groq_client = None

    def chat_completion(
        self,
        messages: List[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """
        Get chat completion from the configured LLM provider.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Optional model override
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Returns:
            Generated text response
        """
        provider = self.provider

        # OpenAI
        if provider == "openai":
            if not self.openai_client:
                raise ValueError("OpenAI API key not configured")
            
            response = self.openai_client.chat.completions.create(
                model=model or settings.openai_main_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content

        # Anthropic Claude
        elif provider == "anthropic":
            if not self.anthropic_client:
                raise ValueError("Anthropic API key not configured")
            
            # Convert messages format for Claude
            system_message = None
            claude_messages = []
            
            for msg in messages:
                if msg["role"] == "system":
                    system_message = msg["content"]
                else:
                    claude_messages.append({
                        "role": msg["role"],
                        "content": msg["content"]
                    })
            
            response = self.anthropic_client.messages.create(
                model=model or settings.claude_model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_message,
                messages=claude_messages,
            )
            return response.content[0].text

        # Groq
        elif provider == "groq":
            if not self.groq_client:
                raise ValueError("Groq API key not configured")
            
            response = self.groq_client.chat.completions.create(
                model=model or settings.groq_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content

        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def extract_json(
        self,
        messages: List[dict],
        model: Optional[str] = None,
    ) -> dict:
        """
        Extract JSON from LLM response (for structured extraction).
        
        Args:
            messages: List of message dicts
            model: Optional model override
            
        Returns:
            Parsed JSON object
        """
        response = self.chat_completion(
            messages=messages,
            model=model,
            temperature=0.3,  # Lower temperature for structured output
            max_tokens=2000,
        )
        
        # Try to extract JSON from response
        try:
            # Look for JSON in code blocks
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                json_str = response.strip()
            
            return json.loads(json_str)
        except (json.JSONDecodeError, IndexError):
            # If no valid JSON found, return empty structure
            return {"memories": []}

    def get_embeddings(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        """
        Generate embeddings for text (currently OpenAI only).
        
        Args:
            texts: List of texts to embed
            model: Optional model override
            
        Returns:
            List of embedding vectors
        """
        if not self.openai_client:
            raise ValueError("OpenAI API key required for embeddings")
        
        response = self.openai_client.embeddings.create(
            input=texts,
            model=model or settings.openai_embedding_model,
        )
        
        return [item.embedding for item in response.data]


# Global client instance
llm_client = UnifiedLLMClient()
