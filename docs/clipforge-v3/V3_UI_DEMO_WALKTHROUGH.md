# ClipForge V3 UI Demo Walkthrough

## Entry

- Open `/v3`
- This page is the guided MVP demo entry for ClipForge 3.0
- It is designed for mock flow only
- Current 0% traffic demo tag URL: `https://v3-ui-demo---clipforge-tools-znaw4q4ldq-uc.a.run.app/v3`
- Demo revision: `clipforge-tools-00115-kay`
- Production traffic remains on the SQLite production revision; this demo tag does not receive default traffic.

## What The Page Shows

- Current mode: mock only
- Database backend
- Storage backend
- Redis status
- Worker status
- Maintenance mode
- Writes enabled

The page also shows a clear warning that no Ark / Seedance paid call will be made.

## Demo Steps

### Step 1: Project

- Create a demo project directly on `/v3`
- Or select an existing V3 project and reopen it inside the guided flow
- The built-in demo product uses the Buffing Wheel sample
- Verified demo action: clicking the demo project path creates a V3 project and returns to `/v3?project_id=...`

### Step 2: Product Info / Image

- Save product info
- Optionally confirm Product Truth in the same step
- Upload a product image
- Or click the built-in demo image button
- Verified demo action: the built-in Buffing Wheel facts and demo image path complete without a paid provider call.

### Step 3: Prompt

- Click `Generate mock prompt`
- The page prepares the first shot prompt for the demo flow
- In mock mode, the page may apply a mock-only prompt override so the non-paid demo can continue even if a real-provider identity-preservation rule would block it
- Verified demo action: mock prompt generation returns to the same guided page and leaves provider mode as `mock`.

### Step 4: Mock Generate

- Click `Submit mock video job`
- This stays inside the mock generation path
- No Ark task is created
- No Seedance paid request is created
- Verified demo action: the mock generate endpoint creates a mock take and reports that cost remains `0`.

### Step 5: Result / Take

- The latest mock take is shown on the same page
- You can preview the local mock video
- The result card shows take id, prompt version, provider, created time, and cost
- Verified demo action: the result card shows `Provider: mock` and `Cost: 0`.

## What Is Real And What Is Mock

- Product, Product Truth, assets, project tables, shots, prompts, takes, and usage rows are real database writes
- The generated video is a local FFmpeg mock video
- Provider is always `mock` in this guided flow
- Cost stays `0`

## Current Limits

- This page is an MVP demo flow, not the full operator console replacement
- The full V3 console still lives at `/v3/projects/{project_id}`
- No real Ark / Seedance submission is allowed from this guided page
- No production traffic was switched as part of this demo pass
- Next front-end step: collect user click feedback from this guided page, then continue simplifying labels, empty states, and recovery messages.
