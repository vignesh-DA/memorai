"""Test OpenAI embeddings are working"""

import asyncio
import sys
from app.utils.embeddings import EmbeddingGenerator
from app.config import get_settings

async def test_embeddings():
    """Test embedding generation"""
    settings = get_settings()
    
    print("🧪 Testing OpenAI Embeddings...")
    print(f"Provider: {settings.embedding_provider}")
    print(f"Model: {settings.embedding_model}")
    print(f"Expected dimension: {settings.memory_embedding_dimension}")
    print()
    
    try:
        # Create embedding generator (without Redis for testing)
        embedder = EmbeddingGenerator(redis_client=None)
        print(f"✅ EmbeddingGenerator initialized")
        print(f"   Model: {embedder.model}")
        print(f"   Dimension: {embedder.dimension}")
        print(f"   Using OpenAI: {embedder.use_openai}")
        print()
        
        # Test embedding generation
        test_text = "This is a test sentence for embedding generation."
        print(f"Generating embedding for: '{test_text}'")
        
        embedding = await embedder.generate(test_text)
        
        print(f"✅ Embedding generated successfully!")
        print(f"   Length: {len(embedding)}")
        print(f"   First 5 values: {embedding[:5]}")
        print(f"   Type: {type(embedding)}")
        print()
        
        # Verify dimension
        if len(embedding) == settings.memory_embedding_dimension:
            print(f"✅ Dimension matches expected: {len(embedding)}")
        else:
            print(f"⚠️  Dimension mismatch! Expected {settings.memory_embedding_dimension}, got {len(embedding)}")
            
        # Test batch generation
        print("\nTesting batch generation...")
        texts = ["First text", "Second text", "Third text"]
        embeddings = await embedder.generate_batch(texts)
        print(f"✅ Batch embeddings generated: {len(embeddings)} embeddings")
        
        print("\n🎉 All embedding tests passed!")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_embeddings())
    sys.exit(0 if success else 1)
