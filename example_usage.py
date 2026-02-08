"""Example usage of the Long-Form Memory System."""

import asyncio
import httpx


BASE_URL = "http://localhost:8000/api/v1"


async def example_conversation_flow():
    """Demonstrate a conversation flow with memory."""
    
    async with httpx.AsyncClient() as client:
        user_id = "demo_user_001"
        
        # Turn 1: User shares preferences
        print("=" * 60)
        print("TURN 1: Initial preferences")
        print("=" * 60)
        
        response = await client.post(
            f"{BASE_URL}/conversation",
            json={
                "user_id": user_id,
                "turn_number": 1,
                "message": "I'm a software engineer who loves Python and I prefer dark mode UIs. I usually work late at night.",
                "include_memories": True,
            }
        )
        result = response.json()
        print(f"User: {result['response'][:200]}...")
        print(f"Processing time: {result['processing_time_ms']:.2f}ms")
        print(f"Memories extracted: {result['memories_extracted']}")
        print()
        
        # Wait for async extraction
        await asyncio.sleep(2)
        
        # Turn 50: General conversation
        print("=" * 60)
        print("TURN 50: General question")
        print("=" * 60)
        
        response = await client.post(
            f"{BASE_URL}/conversation",
            json={
                "user_id": user_id,
                "turn_number": 50,
                "message": "What programming language should I learn next?",
                "include_memories": True,
            }
        )
        result = response.json()
        print(f"Assistant: {result['response'][:200]}...")
        print(f"Memories used: {len(result['memories_used'])}")
        print()
        
        # Turn 100: Preference recall
        print("=" * 60)
        print("TURN 100: Testing preference recall")
        print("=" * 60)
        
        response = await client.post(
            f"{BASE_URL}/conversation",
            json={
                "user_id": user_id,
                "turn_number": 100,
                "message": "What time of day do I usually work?",
                "include_memories": True,
            }
        )
        result = response.json()
        print(f"Assistant: {result['response'][:200]}...")
        print()
        
        # Search memories
        print("=" * 60)
        print("SEARCHING MEMORIES")
        print("=" * 60)
        
        response = await client.post(
            f"{BASE_URL}/memories/{user_id}/search",
            params={"query": "programming", "top_k": 5}
        )
        results = response.json()
        print(f"Found {len(results)} relevant memories:")
        for i, result in enumerate(results, 1):
            memory = result['memory']
            print(f"\n{i}. [{memory['type']}] {memory['content']}")
            print(f"   Relevance: {result['relevance_score']:.2f} | "
                  f"Confidence: {memory['metadata']['confidence']:.2f}")
        print()
        
        # Get stats
        print("=" * 60)
        print("MEMORY STATISTICS")
        print("=" * 60)
        
        response = await client.get(f"{BASE_URL}/memories/{user_id}/stats")
        stats = response.json()
        print(f"Total memories: {stats['total_memories']}")
        print(f"Average confidence: {stats['avg_confidence']:.2f}")
        print(f"Memory types:")
        for mem_type, count in stats['memories_by_type'].items():
            print(f"  - {mem_type}: {count}")
        print()


async def example_memory_management():
    """Demonstrate memory management features."""
    
    async with httpx.AsyncClient() as client:
        user_id = "demo_user_002"
        
        # Create some memories
        print("=" * 60)
        print("CREATING MEMORIES")
        print("=" * 60)
        
        memories = [
            {
                "user_id": user_id,
                "type": "preference",
                "content": "Prefers coffee over tea",
                "source_turn": 1,
                "confidence": 0.9,
                "tags": ["beverage", "preference"],
                "entities": ["coffee", "tea"],
            },
            {
                "user_id": user_id,
                "type": "fact",
                "content": "Lives in San Francisco",
                "source_turn": 5,
                "confidence": 0.95,
                "tags": ["location"],
                "entities": ["San Francisco"],
            },
            {
                "user_id": user_id,
                "type": "commitment",
                "content": "Call mom tomorrow at 3 PM",
                "source_turn": 10,
                "confidence": 0.85,
                "tags": ["reminder", "family"],
                "entities": ["mom"],
            },
        ]
        
        for memory in memories:
            response = await client.post(f"{BASE_URL}/memories", json=memory)
            if response.status_code == 201:
                created = response.json()
                print(f"✓ Created: {created['content']}")
        print()
        
        # List all memories
        print("=" * 60)
        print("LISTING MEMORIES")
        print("=" * 60)
        
        response = await client.get(
            f"{BASE_URL}/memories/{user_id}/list",
            params={"limit": 10}
        )
        all_memories = response.json()
        print(f"Total memories: {len(all_memories)}")
        for memory in all_memories:
            print(f"- [{memory['type']}] {memory['content']}")
        print()
        
        # Optimize memory store
        print("=" * 60)
        print("OPTIMIZING MEMORY STORE")
        print("=" * 60)
        
        response = await client.post(
            f"{BASE_URL}/memories/{user_id}/optimize",
            params={"current_turn": 100}
        )
        results = response.json()
        print("Optimization results:")
        print(f"  - Decay applied: {results['decay_applied']}")
        print(f"  - Consolidations: {results['consolidations']}")
        print(f"  - Conflicts resolved: {results['conflicts_resolved']}")
        print(f"  - Memories cleaned: {results['memories_cleaned']}")
        print()


async def check_health():
    """Check system health."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        health = response.json()
        
        print("=" * 60)
        print("SYSTEM HEALTH")
        print("=" * 60)
        print(f"Status: {health['status'].upper()}")
        print("Services:")
        for service, status in health['services'].items():
            emoji = "✓" if status == "healthy" else "✗"
            print(f"  {emoji} {service}: {status}")
        print()


async def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("LONG-FORM MEMORY SYSTEM - DEMO")
    print("=" * 60 + "\n")
    
    try:
        # Check health first
        await check_health()
        
        # Run conversation flow
        print("\n📝 EXAMPLE 1: Conversation Flow with Memory")
        print("-" * 60)
        await example_conversation_flow()
        
        # Run memory management
        print("\n🔧 EXAMPLE 2: Memory Management")
        print("-" * 60)
        await example_memory_management()
        
        print("\n" + "=" * 60)
        print("✓ Demo completed successfully!")
        print("=" * 60 + "\n")
        
    except httpx.ConnectError:
        print("\n❌ ERROR: Could not connect to the API.")
        print("Make sure the server is running: uvicorn app.main:app --reload")
        print()
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
