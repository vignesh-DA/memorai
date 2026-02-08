"""Lightweight version using only API services (no local databases)."""

import asyncio
from app.config import get_settings

settings = get_settings()


async def test_openai_only():
    """Test only OpenAI API."""
    try:
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        
        response = await client.embeddings.create(
            input="test",
            model=settings.openai_embedding_model,
        )
        
        print("✅ OpenAI API: Working")
        return True
    except openai.RateLimitError:
        print("⚠️  OpenAI API: Rate limited (but key is valid)")
        return True
    except Exception as e:
        print(f"❌ OpenAI API: {e}")
        return False


async def test_pinecone_only():
    """Test only Pinecone API."""
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=settings.pinecone_api_key)
        pc.list_indexes()
        print("✅ Pinecone API: Working")
        return True
    except Exception as e:
        print(f"❌ Pinecone API: {e}")
        return False


async def main():
    """Run minimal validation."""
    print("\n" + "=" * 60)
    print("Minimal API Validation (No Docker Required)")
    print("=" * 60 + "\n")
    
    openai_ok = await test_openai_only()
    pinecone_ok = await test_pinecone_only()
    
    print("\n" + "=" * 60)
    if openai_ok and pinecone_ok:
        print("✅ Core APIs working! You can proceed without Docker.")
        print("\nNote: Full system requires PostgreSQL + Redis.")
        print("For now, you can test with in-memory storage.")
    else:
        print("❌ Fix API keys before proceeding.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
