# /// script
# dependencies = ["requests"]
# ///

import requests
import json
import sys

def test_api():
    url = "http://localhost:8000/neighbours"
    
    # Polygon from existing tests (Budapest - Chain Bridge area)
    payload = {
        "geojson": {
            "type": "Polygon",
            "coordinates": [
                [
                    [19.040, 47.498],
                    [19.045, 47.498],
                    [19.045, 47.502],
                    [19.040, 47.502],
                    [19.040, 47.498]
                ]
            ]
        },
        "k": 5,
        "nprobe": 32
    }

    print(f"Sending POST request to {url}...")
    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("\nResponse Headers:")
            for k, v in response.headers.items():
                print(f"  {k}: {v}")
                
            data = response.json()
            print("\nResponse Body (truncated):")
            print(json.dumps(data, indent=2)[:500] + "...")
            
            if "neighbours" in data:
                print(f"\nSuccess! Found {len(data['neighbours']['features'])} neighbours.")
            else:
                print("\nWarning: 'neighbours' key missing in response.")
        else:
            print("\nError Response:")
            print(response.text)
            sys.exit(1)

    except requests.exceptions.ConnectionError:
        print("\nError: Could not connect to the server.")
        print("Make sure the backend is running (try './run_backend_local.sh' in another terminal).")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_api()
