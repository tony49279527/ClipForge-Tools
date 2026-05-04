import json

task_result = {'id': 'cgt-20260504174117-snfln', 'model': 'doubao-seedance-1-0-pro-250528', 'status': 'succeeded', 'content': {'video_url': 'https://ark-content-generation'}, 'usage': {'completion_tokens': 103818, 'total_tokens': 103818}, 'created_at': 1777887684, 'updated_at': 1777887718, 'seed': 87093, 'resolution': '720p', 'ratio': '9:16', 'duration': 5, 'framespersecond': 24, 'service_tier': 'default', 'execution_expires_after': 172800, 'draft': False}

def extract_video_url(task_result):
    content = task_result.get("content") or {}
    if isinstance(content, dict) and content.get("video_url"):
        return content["video_url"]

    output = task_result.get("output") or {}
    if isinstance(output, dict):
        if output.get("video_url"):
            return output["video_url"]
        videos = output.get("videos") or []
        if videos and isinstance(videos[0], dict) and videos[0].get("url"):
            return videos[0]["url"]
    if task_result.get("video_url"):
        return task_result["video_url"]
    raise RuntimeError(f"Could not extract video URL from Seedance result: {task_result}")

try:
    print("Extracted:", extract_video_url(task_result))
except Exception as e:
    print("Error:", repr(e))

