import sys
import time
import httpx

def test_simple():
    print("Starting simple test...")
    print(f"Python version: {sys.version}")
    
    try:
        print("Creating HTTP client...")
        client = httpx.Client()
        
        print("Testing Ollama API...")
        response = client.post(
            "http://localhost:11434/api/generate",
            json={"model": "tinyllama:latest", "prompt": "test", "stream": False},
            timeout=30
        )
        
        print(f"Status code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Response keys: {list(data.keys())}")
            print("✅ API test successful")
        else:
            print(f"❌ API test failed: {response.text}")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_simple()
