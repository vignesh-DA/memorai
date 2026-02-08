"""Test script for multi-provider LLM setup."""
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

print("=" * 60)
print("🚀 Testing Multi-Provider LLM Setup")
print("=" * 60)

# Check configuration
from app.config import get_settings
settings = get_settings()

print(f"\n📋 Configuration:")
print(f"  LLM Provider: {settings.llm_provider}")
print(f"  Embedding Provider: {settings.embedding_provider}")

# Check API keys
print(f"\n🔑 API Keys Status:")
has_openai = bool(settings.openai_api_key)
has_groq = bool(settings.groq_api_key and settings.groq_api_key != "your_groq_api_key_here")
has_claude = bool(settings.anthropic_api_key and settings.anthropic_api_key != "your_anthropic_api_key_here")

print(f"  ✅ OpenAI: {'Configured' if has_openai else '❌ Missing'}")
print(f"  {'✅' if has_groq else '❌'} Groq: {'Configured' if has_groq else 'Missing'}")
print(f"  {'✅' if has_claude else '⏳'} Claude: {'Configured' if has_claude else 'Pending (will add later)'}")

# Test Groq
if has_groq and settings.llm_provider == "groq":
    print(f"\n🧪 Testing Groq API...")
    try:
        from groq import Groq
        client = Groq(api_key=settings.groq_api_key)
        
        # Simple test
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "user", "content": "Say 'Hello from Groq!' in one sentence."}
            ],
            max_tokens=50,
        )
        
        print(f"  ✅ Groq API Working!")
        print(f"  Model: {settings.groq_model}")
        print(f"  Response: {response.choices[0].message.content}")
        
    except Exception as e:
        print(f"  ❌ Groq Error: {e}")

# Test unified LLM client
print(f"\n🔄 Testing Unified LLM Client...")
try:
    from app.llm_client import llm_client
    
    response = llm_client.chat_completion(
        messages=[
            {"role": "user", "content": "Reply with exactly: 'Memory AI system ready!'"}
        ],
        max_tokens=50,
    )
    
    print(f"  ✅ Unified Client Working!")
    print(f"  Provider: {settings.llm_provider}")
    print(f"  Response: {response}")
    
except Exception as e:
    print(f"  ❌ Unified Client Error: {e}")

# Test memory extraction (JSON output)
print(f"\n🧠 Testing Memory Extraction Format...")
try:
    test_result = llm_client.extract_json(
        messages=[
            {"role": "system", "content": "Extract memories from conversation. Return JSON with 'memories' array."},
            {"role": "user", "content": "User: I love pizza. Assistant: Great! I'll remember that."}
        ]
    )
    
    print(f"  ✅ JSON Extraction Working!")
    print(f"  Extracted: {test_result}")
    
except Exception as e:
    print(f"  ❌ Extraction Error: {e}")

# Summary
print(f"\n" + "=" * 60)
print(f"📊 Summary:")
print(f"  Current Provider: {settings.llm_provider.upper()}")
print(f"  Status: {'✅ READY' if has_groq or has_claude else '❌ Need API Keys'}")
print(f"  Next: {'Run server with: uvicorn app.main:app --reload' if has_groq else 'Add API keys and retry'}")
print("=" * 60)
