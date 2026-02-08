"""Test script to verify the complete memory flow."""

import asyncio
import requests
import time

API_BASE = "http://127.0.0.1:8000/api/v1"
USER_ID = "test_user_001"

def send_message(message: str, turn: int):
    """Send a message and get response."""
    response = requests.post(
        f"{API_BASE}/conversation",
        json={
            "user_id": USER_ID,
            "message": message,
            "turn_number": turn,
            "include_memories": True
        }
    )
    return response.json()

def get_stats():
    """Get memory stats."""
    response = requests.get(f"{API_BASE}/stats/{USER_ID}")
    return response.json()

def search_memories(query: str):
    """Search memories."""
    response = requests.post(
        f"{API_BASE}/memories/search",
        json={
            "user_id": USER_ID,
            "query": query,
            "top_k": 5
        }
    )
    return response.json()

def main():
    print("🧠 Testing Long-Form Memory System\n")
    
    # Test 1: Send messages with extractable memories
    print("Test 1: Sending messages with memories...")
    
    messages = [
        "Hi, my name is Alex and I'm a software engineer",
        "I love Python programming and machine learning",
        "Tomorrow I have a meeting at 3 PM",
        "I prefer dark mode over light mode"
    ]
    
    for i, msg in enumerate(messages, 1):
        print(f"\n📝 Turn {i}: {msg}")
        result = send_message(msg, i)
        print(f"✅ Response: {result['response'][:100]}...")
        print(f"⏱️  Processing time: {result['processing_time_ms']:.0f}ms")
        print(f"📊 Memories used: {result['memories_used']}")
        
        # Wait for background extraction
        time.sleep(2)
    
    # Test 2: Check stats
    print("\n" + "="*60)
    print("Test 2: Checking memory stats...")
    time.sleep(3)  # Give time for background tasks
    
    stats = get_stats()
    print(f"\n📈 Memory Statistics:")
    print(f"   Total memories: {stats['total_memories']}")
    print(f"   By type: {stats['by_type']}")
    print(f"   Last 7 days: {stats['recent_activity']['last_7_days']}")
    
    # Test 3: Search memories
    print("\n" + "="*60)
    print("Test 3: Searching memories...")
    
    search_queries = ["name", "programming", "meeting", "preferences"]
    
    for query in search_queries:
        print(f"\n🔍 Searching for: '{query}'")
        results = search_memories(query)
        print(f"   Found {len(results)} memories:")
        for r in results[:3]:
            print(f"   - {r['memory']['content'][:60]}... (score: {r['score']:.2f})")
    
    # Test 4: Test memory recall
    print("\n" + "="*60)
    print("Test 4: Testing memory recall...")
    
    recall_msg = "What's my name and what do I do?"
    print(f"\n💬 Question: {recall_msg}")
    result = send_message(recall_msg, 5)
    print(f"🤖 Response: {result['response']}")
    print(f"📚 Used {len(result['memories_used'])} memories")
    
    print("\n" + "="*60)
    print("✅ All tests completed!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
