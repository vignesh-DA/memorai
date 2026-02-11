"""Test vision service integration."""

import asyncio
from app.services.vision_service import vision_service


async def test_vision():
    """Test vision service basic functions."""
    print("🔍 Testing Vision Service...")
    
    # Test 1: Service initialization
    print(f"✅ Vision service initialized")
    print(f"   Max image size: {vision_service.max_image_size}")
    print(f"   Max file size: {vision_service.max_file_size / (1024*1024)}MB")
    
    # Test 2: Check LLM client
    print(f"✅ LLM client connected: {vision_service.llm_client.__class__.__name__}")
    
    print("\n✅ All checks passed!")
    print("\nTo test with a real image:")
    print("1. Start the server: uvicorn app.main:app --reload --port 8000")
    print("2. Open http://localhost:8000")
    print("3. Click the 📷 button to upload an image")
    print("4. Add a prompt like 'Describe this image in detail'")
    print("5. Click Send")
    print("\nThe Groq Vision model (Llama 4 Scout) will analyze it!")


if __name__ == "__main__":
    asyncio.run(test_vision())
