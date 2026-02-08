"""Quick test script to verify OpenAI API key."""
import os
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# Check if API key is loaded
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("❌ OPENAI_API_KEY not found in .env file")
    exit(1)

print(f"✅ API Key loaded: {api_key[:20]}...")

# Test API connection
try:
    client = OpenAI()
    
    # Simple test: List models
    print("\n🔄 Testing OpenAI connection...")
    models = client.models.list()
    print(f"✅ Connected successfully!")
    print(f"✅ Found {len(models.data)} models available")
    
    # Test embedding
    print("\n🔄 Testing embedding generation...")
    response = client.embeddings.create(
        input="Test message",
        model="text-embedding-3-small"
    )
    print(f"✅ Embedding generated: {len(response.data[0].embedding)} dimensions")
    
    print("\n✨ All OpenAI tests passed!")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nThis might be a rate limit issue. Wait a few moments and try again.")
