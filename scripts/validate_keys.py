"""Validate all API keys and service connections."""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings

settings = get_settings()


def print_status(service: str, status: bool, message: str = ""):
    """Print service status with emoji."""
    emoji = "✅" if status else "❌"
    status_text = "Working" if status else "Failed"
    print(f"{emoji} {service}: {status_text}")
    if message:
        print(f"   {message}")


async def test_openai():
    """Test OpenAI API key."""
    try:
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        
        # Test embedding
        response = await client.embeddings.create(
            input="test",
            model=settings.openai_embedding_model,
        )
        
        if response.data and len(response.data[0].embedding) == 1536:
            print_status("OpenAI API", True, "Embeddings working")
            return True
        else:
            print_status("OpenAI API", False, "Unexpected response format")
            return False
            
    except openai.AuthenticationError:
        print_status("OpenAI API", False, "Invalid API key")
        return False
    except openai.RateLimitError:
        print_status("OpenAI API", False, "Rate limit exceeded")
        return False
    except Exception as e:
        print_status("OpenAI API", False, f"Error: {str(e)}")
        return False


async def test_pinecone():
    """Test Pinecone API key."""
    try:
        from pinecone import Pinecone
        
        pc = Pinecone(api_key=settings.pinecone_api_key)
        
        # List indexes
        indexes = pc.list_indexes()
        
        print_status("Pinecone API", True, f"Connected to {settings.pinecone_environment}")
        
        # Check if our index exists
        index_names = [idx.name for idx in indexes]
        if settings.pinecone_index_name in index_names:
            print(f"   📦 Index '{settings.pinecone_index_name}' exists")
        else:
            print(f"   ⚠️  Index '{settings.pinecone_index_name}' not found - will be created on first use")
        
        return True
        
    except Exception as e:
        error_msg = str(e)
        if "INVALID_API_KEY" in error_msg or "Unauthorized" in error_msg:
            print_status("Pinecone API", False, "Invalid API key")
        else:
            print_status("Pinecone API", False, f"Error: {error_msg}")
        return False


async def test_postgres():
    """Test PostgreSQL connection."""
    try:
        import asyncpg
        
        conn = await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            timeout=5,
        )
        
        # Test query
        result = await conn.fetchval("SELECT 1")
        await conn.close()
        
        if result == 1:
            print_status("PostgreSQL", True, f"Connected to {settings.postgres_host}:{settings.postgres_port}")
            
            # Check if pgvector is installed
            conn = await asyncpg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
            )
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                print("   📦 pgvector extension available")
            except:
                print("   ⚠️  pgvector extension not available")
            finally:
                await conn.close()
            
            return True
        return False
        
    except asyncpg.InvalidPasswordError:
        print_status("PostgreSQL", False, "Invalid password")
        return False
    except asyncpg.InvalidCatalogNameError:
        print_status("PostgreSQL", False, f"Database '{settings.postgres_db}' does not exist")
        print("   Run: docker-compose up -d postgres")
        return False
    except Exception as e:
        error_msg = str(e)
        if "Connection refused" in error_msg:
            print_status("PostgreSQL", False, "Connection refused - is PostgreSQL running?")
            print("   Run: docker-compose up -d postgres")
        else:
            print_status("PostgreSQL", False, f"Error: {error_msg}")
        return False


async def test_redis():
    """Test Redis connection."""
    try:
        from redis import asyncio as aioredis
        
        redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        
        # Test ping
        result = await redis.ping()
        await redis.close()
        
        if result:
            print_status("Redis", True, f"Connected to {settings.redis_host}:{settings.redis_port}")
            return True
        return False
        
    except Exception as e:
        error_msg = str(e)
        if "Connection refused" in error_msg:
            print_status("Redis", False, "Connection refused - is Redis running?")
            print("   Run: docker-compose up -d redis")
        else:
            print_status("Redis", False, f"Error: {error_msg}")
        return False


async def main():
    """Run all validation tests."""
    print("\n" + "=" * 60)
    print("API Keys & Services Validation")
    print("=" * 60 + "\n")
    
    print("Testing API Keys...")
    print("-" * 60)
    
    results = []
    
    # Test OpenAI
    openai_ok = await test_openai()
    results.append(("OpenAI", openai_ok))
    
    # Test Pinecone
    pinecone_ok = await test_pinecone()
    results.append(("Pinecone", pinecone_ok))
    
    print("\nTesting Database Connections...")
    print("-" * 60)
    
    # Test PostgreSQL
    postgres_ok = await test_postgres()
    results.append(("PostgreSQL", postgres_ok))
    
    # Test Redis
    redis_ok = await test_redis()
    results.append(("Redis", redis_ok))
    
    # Summary
    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    
    if passed == total:
        print(f"✅ All {total} services validated successfully!")
        print("\n🚀 You're ready to start the application:")
        print("   uvicorn app.main:app --reload")
    else:
        print(f"⚠️  {passed}/{total} services working")
        print("\nFailed services:")
        for service, ok in results:
            if not ok:
                print(f"  ❌ {service}")
        print("\nPlease fix the issues above before starting the application.")
    
    print("=" * 60 + "\n")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
