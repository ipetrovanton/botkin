import httpx
import time

def test_api():
    print("Testing Ollama API...")
    try:
        r = httpx.post('http://localhost:11434/api/generate', 
                      json={'model': 'qwen3:14b', 'prompt': 'test', 'stream': False}, 
                      timeout=30)
        print(f"Status: {r.status_code}")
        print(f"Response: {r.json()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_api()
