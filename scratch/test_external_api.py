import requests
import json

BASE_URL = "http://127.0.0.1:8080/api/external/jobs"
HEADERS = {
    "X-API-Key": "lobster_secret_key",
    "Content-Type": "application/json"
}

PAYLOAD = {
    "project_name": "API External Test",
    "product_name": "Screwdriver Kit",
    "product_brief": "A professional magnetic screwdriver kit with 24 precision bits.",
    "video_mode": "shorts",
    "ratio": "9:16",
    "clip_duration": 5,
    "clip_count": 1,
    "resolution": "720p",
    "youtube_title": "Best Magnetic Screwdriver Kit 2026",
    "youtube_description": "Auto-submitted via external API for testing.",
    "privacy": "private",
    "upload_to_youtube": False,
    "stitch_final_video": True
}

def test_unauthorized():
    print("Testing unauthorized request (missing key)...")
    resp = requests.post(BASE_URL, json=PAYLOAD)
    print("Status:", resp.status_code)
    print("Response:", resp.json())
    assert resp.status_code == 401

def test_invalid_data():
    print("\nTesting invalid data (missing required field)...")
    bad_payload = PAYLOAD.copy()
    del bad_payload["project_name"]
    resp = requests.post(BASE_URL, json=bad_payload, headers=HEADERS)
    print("Status:", resp.status_code)
    assert resp.status_code == 422

def test_authorized_submission():
    print("\nTesting valid authorized submission...")
    resp = requests.post(BASE_URL, json=PAYLOAD, headers=HEADERS)
    print("Status:", resp.status_code)
    data = resp.json()
    print("Response:", data)
    assert resp.status_code == 200
    assert data["status"] == "success"
    print(f"🎉 Job successfully enqueued! Job ID: {data['job_id']}")

if __name__ == "__main__":
    try:
        test_unauthorized()
        test_invalid_data()
        test_authorized_submission()
        print("\n✅ All local API tests passed successfully!")
    except AssertionError:
        print("\n❌ Test assertion failed.")
    except Exception as e:
        print("\n❌ Error running tests:", e)
