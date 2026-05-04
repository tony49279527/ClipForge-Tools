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
    creds = flow.run_local_server(port=0)
    
    # Save the credentials to a file
    with open("youtube_token.json", "w", encoding="utf-8") as f:
        f.write(creds.to_json())
        
    print("✅ Authorization successful!")
    print("The 'youtube_token.json' file has been generated in the current directory.")
    print("You can now open it, copy its contents, and paste them into the YOUTUBE_TOKEN_JSON environment variable.")

if __name__ == "__main__":
    main()
