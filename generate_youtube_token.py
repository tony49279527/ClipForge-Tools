import json
import os
from google_auth_oauthlib.flow import InstalledAppFlow

# YouTube Data API v3 upload scope
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def main():
    if not os.path.exists("client_secret.json"):
        print("❌ Error: 'client_secret.json' not found in the current directory.")
        print("Please download it from Google Cloud Console and place it here.")
        return

    print("🚀 Starting YouTube authorization flow...")
    print("A browser window will open. Please log in and authorize the app.")
    
    # Initialize the flow
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    
    # Run the local server to get the credentials
    # prompt='consent' forces Google to issue a new refresh_token even if the user has authorized before
    creds = flow.run_local_server(port=0, prompt='consent')
    
    # Extract data for environment variables
    with open("client_secret.json", "r", encoding="utf-8") as f:
        client_config = json.load(f)
        web_config = client_config.get("web") or client_config.get("installed") or {}
        client_id = web_config.get("client_id", "")
        client_secret = web_config.get("client_secret", "")

    print("\n✅ Authorization successful!\n")
    print("=" * 60)
    print("🎉 Please copy the following values into your platform's Environment Variables:\n")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)
    print("\nNote: You can safely ignore GOOGLE_REDIRECT_URI or set it to whatever you want, as it is only needed for the web flow.")

if __name__ == "__main__":
    main()
