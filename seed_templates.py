"""
Seed the templates table with pre-built video templates for hardware tools
and other e-commerce categories. Each template = a proven video concept.
"""
from db import create_template, get_conn, init_db

TEMPLATES = [
    # ── Polishing / Buffing ──
    {
        "name": "抛光轮 - 镜面效果对比",
        "project_name": "Polishing Wheel Demo",
        "product_name": "Cotton Polishing Wheel Kit",
        "simple_idea": (
            "Show a dull, oxidized metal surface. Demonstrate 4-step polishing process "
            "using the kit: rough buff → medium polish → fine finish → mirror shine. "
            "End with side-by-side before/after comparison."
        ),
        "target_audience": "DIY enthusiasts, garage hobbyists, auto detailers, RV owners",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Clean workshop setting. Good lighting on the metal surface to show reflection. Text overlays for each step.",
        "youtube_title": "From Rust to Mirror Shine - 4 Step Polishing Demo",
        "youtube_description": (
            "Watch a complete polishing workflow from dull metal to mirror finish. "
            "Perfect for auto restoration, RV maintenance, and garage DIY. "
            "Product link in description."
        ),
        "privacy": "unlisted",
    },
    {
        "name": "抛光轮 - 快速 Shorts",
        "project_name": "Polishing Wheel Shorts",
        "product_name": "Quick Polish Wheel Attachment",
        "simple_idea": (
            "Fast 30-second transformation: hook with a shocking before shot, "
            "quick 3-step polish, reveal the mirror result. Fast cuts, no talking, "
            "text overlays only."
        ),
        "target_audience": "TikTok/Shorts audience, DIY beginners, tool enthusiasts",
        "video_mode": "shorts",
        "ratio": "9:16",
        "clip_count": 3,
        "clip_duration": 6,
        "resolution": "720p",
        "style_preference": "Fast-paced vertical video. Bold text overlays. Satisfying transformation reveal.",
        "youtube_title": "Satisfying Metal Polish in 30 Seconds",
        "youtube_description": "Quick polishing demo. Full tutorial video on our channel.",
        "privacy": "unlisted",
    },
    # ── Wrenches / Sockets ──
    {
        "name": "扳手套装 - 场景演示",
        "project_name": "Wrench Set Demo",
        "product_name": "Professional Ratcheting Wrench Set",
        "simple_idea": (
            "Show 3 common garage problems: stuck bolt, tight space, rounded nut. "
            "Demonstrate how the ratcheting wrench solves each one. Close-up on the "
            "72-tooth mechanism in action."
        ),
        "target_audience": "DIY mechanics, auto enthusiasts, home garage owners",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Garage/workshop setting. Close-up shots of the tool in use. Text callouts for features.",
        "youtube_title": "3 Garage Problems This Wrench Set Solves Instantly",
        "youtube_description": (
            "See a professional ratcheting wrench set tackle stuck bolts, tight spaces, "
            "and rounded nuts. Complete set review and demo."
        ),
        "privacy": "unlisted",
    },
    {
        "name": "扳手 - 尺寸对比 Shorts",
        "project_name": "Wrench Size Guide",
        "product_name": "Metric & SAE Wrench Set",
        "simple_idea": (
            "Lay out all wrench sizes side by side. Quick cuts showing each size "
            "on its matching bolt. End with full set overview."
        ),
        "target_audience": "New DIYers, tool buyers comparing options",
        "video_mode": "shorts",
        "ratio": "9:16",
        "clip_count": 3,
        "clip_duration": 6,
        "resolution": "720p",
        "style_preference": "Clean flat lay. Organized layout. Quick transitions.",
        "youtube_title": "Every Wrench Size You Need - Complete Set",
        "youtube_description": "Full metric and SAE wrench set overview. Link in bio.",
        "privacy": "unlisted",
    },
    # ── Power Drills ──
    {
        "name": "电钻 - 场景用途展示",
        "project_name": "Cordless Drill Demo",
        "product_name": "20V Brushless Cordless Drill",
        "simple_idea": (
            "Show the drill in 4 different use cases: drilling wood, driving screws, "
            "mixing paint, and masonry drilling. Highlight brushless motor efficiency "
            "and battery life indicator."
        ),
        "target_audience": "Homeowners, DIY beginners, woodworkers, contractors",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Bright workshop. Each use case clearly labeled. Speed comparison overlay.",
        "youtube_title": "4 Ways to Use a Cordless Drill You Didn't Know",
        "youtube_description": (
            "From basic drilling to mixing paint — see how versatile a 20V brushless "
            "cordless drill can be in your workshop."
        ),
        "privacy": "unlisted",
    },
    # ── RV / Automotive ──
    {
        "name": "RV 工具 - 应急套装",
        "project_name": "RV Emergency Tool Kit",
        "product_name": "RV Essential Tool Set",
        "simple_idea": (
            "Set the scene: RV breakdown on the road. Walk through 5 tools every "
            "RV owner should carry. Show each tool in a real roadside scenario."
        ),
        "target_audience": "RV owners, campers, road trip enthusiasts, outdoor adventurers",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Outdoor/RV park setting. Natural lighting. Practical demonstration style.",
        "youtube_title": "5 Tools Every RV Owner Must Carry - Roadside Emergency Kit",
        "youtube_description": (
            "Don't get stranded. See the 5 essential tools for RV roadside repairs "
            "and emergency situations. Product list in description."
        ),
        "privacy": "unlisted",
    },
    # ── Garage Storage / Organization ──
    {
        "name": "车库收纳 - 前后对比",
        "project_name": "Garage Organization System",
        "product_name": "Modular Garage Storage System",
        "simple_idea": (
            "Before: messy, cluttered garage. Step-by-step installation of the storage "
            "system. After: perfectly organized tools and equipment. Time-lapse the "
            "transformation."
        ),
        "target_audience": "Homeowners, garage enthusiasts, organization lovers",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Real garage setting. Time-lapse segments. Satisfying organization reveal.",
        "youtube_title": "Messy Garage to Perfect Workshop - Organization Transformation",
        "youtube_description": (
            "See a complete garage transformation using a modular storage system. "
            "Before and after will shock you."
        ),
        "privacy": "unlisted",
    },
    # ── Hand Tools / General ──
    {
        "name": "通用五金 - 开箱测评",
        "project_name": "Tool Unboxing & Review",
        "product_name": "Professional Tool Kit",
        "simple_idea": (
            "Unbox the product. Close-ups of build quality and materials. "
            "Real-world test: use it on an actual project. Honest assessment."
        ),
        "target_audience": "Tool buyers, DIYers researching before purchase",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Clean workbench. Good macro close-ups. Natural voiceover style.",
        "youtube_title": "Honest Review After 30 Days of Use - Tool Kit Unboxing",
        "youtube_description": (
            "Not a paid review. We used this tool kit for 30 days in a real workshop. "
            "Here's what we found."
        ),
        "privacy": "unlisted",
    },
    # ── Automotive Detailing ──
    {
        "name": "汽车美容 - 使用教程",
        "project_name": "Car Detailing Product Demo",
        "product_name": "Professional Ceramic Coating Kit",
        "simple_idea": (
            "Show a dirty car. Step-by-step application: wash → clay bar → polish → "
            "ceramic coating. End with water beading test and final shine reveal."
        ),
        "target_audience": "Car enthusiasts, auto detailers, car owners",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Outdoor driveway or detailing bay. Water beading shots. Glossy finish reveal.",
        "youtube_title": "DIY Ceramic Coating Guide - Professional Results at Home",
        "youtube_description": (
            "Step-by-step ceramic coating application. Save money and get professional "
            "results in your own driveway."
        ),
        "privacy": "unlisted",
    },
    # ── Garden / Outdoor ──
    {
        "name": "园艺工具 - 使用对比",
        "project_name": "Garden Tool Demo",
        "product_name": "Heavy Duty Pruning Shears Set",
        "simple_idea": (
            "Before: overgrown bushes and dead branches. Demonstrate pruning with "
            "the shears on different plant types. After: clean, healthy garden. "
            "Compare with cheap shears side by side."
        ),
        "target_audience": "Gardeners, homeowners, landscapers, plant enthusiasts",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Bright garden setting. Close-ups on clean cuts. Natural lighting.",
        "youtube_title": "Cheap vs Pro Pruning Shears - The Difference Is Obvious",
        "youtube_description": (
            "Side-by-side comparison of budget and professional pruning shears. "
            "See why quality tools make gardening easier."
        ),
        "privacy": "unlisted",
    },
    # ── Kitchen / Home ──
    {
        "name": "厨房工具 - 使用演示",
        "project_name": "Kitchen Gadget Demo",
        "product_name": "Multi-Purpose Kitchen Tool",
        "simple_idea": (
            "Show 3 kitchen tasks that are normally annoying. Demonstrate how the "
            "gadget makes each one fast and easy. Clean, bright kitchen setting."
        ),
        "target_audience": "Home cooks, kitchen enthusiasts, busy parents",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Bright modern kitchen. Clean countertop. Close-up food shots.",
        "youtube_title": "This Kitchen Gadget Replaces 3 Tools - Must Have",
        "youtube_description": (
            "Save counter space and prep time with this versatile kitchen tool. "
            "3 use cases you'll use every day."
        ),
        "privacy": "unlisted",
    },
    # ── Fitness / Sports ──
    {
        "name": "健身器材 - 动作教学",
        "project_name": "Fitness Equipment Demo",
        "product_name": "Adjustable Dumbbell Set",
        "simple_idea": (
            "Show 4 exercises you can do with adjustable dumbbells. Proper form "
            "demonstration for each. Show the quick weight-change mechanism."
        ),
        "target_audience": "Fitness enthusiasts, home gym owners, beginners",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Clean home gym setting. Good lighting on form. Text overlays for rep counts.",
        "youtube_title": "4 Full Body Exercises with Adjustable Dumbbells - Proper Form Guide",
        "youtube_description": (
            "Build a complete home workout with just one set of adjustable dumbbells. "
            "Proper form and common mistakes to avoid."
        ),
        "privacy": "unlisted",
    },
    # ── Electronics / Tech ──
    {
        "name": "电子产品 - 功能拆解",
        "project_name": "Tech Product Review",
        "product_name": "Wireless Earbuds Pro",
        "simple_idea": (
            "Unbox → design overview → key features demo (noise canceling test, "
            "battery life, water resistance) → sound quality comparison → verdict."
        ),
        "target_audience": "Tech enthusiasts, commuters, audio lovers",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Clean desk setup. Macro close-ups. Screen recordings for app features.",
        "youtube_title": "Are These Budget Earbuds Better Than Premium? Full Review",
        "youtube_description": (
            "Comprehensive review covering noise canceling, battery life, sound quality, "
            "and real-world use. Timestamps in description."
        ),
        "privacy": "unlisted",
    },
    # ── Pet Supplies ──
    {
        "name": "宠物用品 - 使用展示",
        "project_name": "Pet Product Demo",
        "product_name": "Premium Pet Grooming Kit",
        "simple_idea": (
            "Show a pet that needs grooming. Demonstrate each tool in the kit: "
            "brush, nail clipper, deshedding tool. Happy clean pet at the end."
        ),
        "target_audience": "Pet owners, dog/cat lovers, pet groomers",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Bright home setting. Pet-friendly atmosphere. Close-ups of tools in use.",
        "youtube_title": "Professional Pet Grooming at Home - Complete Kit Demo",
        "youtube_description": (
            "Save money on grooming with this complete at-home kit. "
            "Suitable for dogs and cats of all sizes."
        ),
        "privacy": "unlisted",
    },
    # ── Generic / Customizable ──
    {
        "name": "通用 - Before/After 对比",
        "project_name": "Product Transformation Demo",
        "product_name": "Your Product Name Here",
        "simple_idea": (
            "Hook with the problem or pain point. Introduce the product as the solution. "
            "Show the transformation or result. End with a clear call to action."
        ),
        "target_audience": "General consumers, DIY enthusiasts",
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_count": 4,
        "clip_duration": 10,
        "resolution": "720p",
        "style_preference": "Clean and professional setting. Focus on the product and results.",
        "youtube_title": "This Product Changed Everything - Before & After Demo",
        "youtube_description": "See the transformation for yourself. Product link in description.",
        "privacy": "unlisted",
    },
]


def seed():
    init_db()
    for tpl in TEMPLATES:
        tid = create_template(tpl)
        print(f"✅ {tid}: {tpl['name']}")
    print(f"\n共创建 {len(TEMPLATES)} 个模板")


if __name__ == "__main__":
    seed()
