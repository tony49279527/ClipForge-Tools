# ClipForge V3 UI Demo Walkthrough

## Entry

- Open `/v3`
- This page is the guided MVP demo entry for ClipForge 3.0
- It is designed for mock flow only

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

### Step 2: Product Info / Image

- Save product info
- Optionally confirm Product Truth in the same step
- Upload a product image
- Or click the built-in demo image button

### Step 3: Prompt

- Click `Generate mock prompt`
- The page prepares the first shot prompt for the demo flow
- In mock mode, the page may apply a mock-only prompt override so the non-paid demo can continue even if a real-provider identity-preservation rule would block it

### Step 4: Mock Generate

- Click `Submit mock video job`
- This stays inside the mock generation path
- No Ark task is created
- No Seedance paid request is created

### Step 5: Result / Take

- The latest mock take is shown on the same page
- You can preview the local mock video
- The result card shows take id, prompt version, provider, created time, and cost

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
