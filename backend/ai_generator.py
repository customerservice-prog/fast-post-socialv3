"""
FastPost Social v3 - AI Content Generator
Uses OpenAI GPT-4o to generate 3 daily social media posts per account
Post types: Morning Promo, Mid-day Tip, Evening Social Proof
"""

import os
import json
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional
from openai import OpenAI


class AIContentGenerator:
      def __init__(self, api_key: str):
                self.client = OpenAI(api_key=api_key)
                self.model = "gpt-4o"

          # Daily post schedule times
                self.schedule = {
                    "morning":   "09:00",
                    "afternoon": "13:00",
                    "evening":   "18:00",
                }

      def generate_daily_posts(
                self,
                business_name: str,
                business_url: str,
                platform: str,
                crawl_data: Optional[Dict] = None
      ) -> List[Dict]:
                """
                        Generate 3 posts for today: morning promo, afternoon tip, evening social proof.
                                Returns list of post dicts ready to insert into DB.
                                        """
                today = date.today().strftime("%Y-%m-%d")
                business_context = self._build_context(business_name, business_url, crawl_data)
                platform_context = self._platform_context(platform)

          posts = []

        post_types = [
                      {
                                        "type": "morning_promo",
                                        "time": self.schedule["morning"],
                                        "instruction": (
                                                              "Write a morning PROMOTIONAL post. Highlight a specific service or product, "
                                                              "create urgency, include a call-to-action like 'Book now' or 'DM us to reserve'. "
                                                              "Use 2-3 relevant emojis. Keep it under 150 words."
                                        )
                      },
                      {
                                        "type": "afternoon_tip",
                                        "time": self.schedule["afternoon"],
                                        "instruction": (
                                                              "Write a mid-day TIPS/VALUE post. Share a helpful tip related to the business "
                                                              "(e.g. 'How to pick the right bounce house for your party size'). "
                                                              "Make it educational and shareable. 2-3 emojis. Under 120 words."
                                        )
                      },
                      {
                                        "type": "evening_proof",
                                        "time": self.schedule["evening"],
                                        "instruction": (
                                                              "Write an evening SOCIAL PROOF post. Simulate a happy customer story or "
                                                              "highlight a recent event the business helped with. "
                                                              "Make it warm, community-focused, include a question to drive comments. "
                                                              "2-3 emojis. Under 130 words."
                                        )
                      }
        ]

        for pt in post_types:
                      try:
                                        caption, image_prompt = self._generate_post(
                                                              business_context=business_context,
                                                              platform_context=platform_context,
                                                              instruction=pt["instruction"],
                                                              post_type=pt["type"]
                                        )
                                        scheduled_time = f"{today} {pt['time']}:00"
                                        posts.append({
                                            "type": pt["type"],
                                            "caption": caption,
                                            "image_prompt": image_prompt,
                                            "scheduled_time": scheduled_time,
                                        })
except Exception as e:
                print(f"[AI Generator] Error generating {pt['type']}: {e}")
                # Fallback post
                posts.append({
                                      "type": pt["type"],
                                      "caption": self._fallback_caption(business_name, pt["type"]),
                                      "image_prompt": f"Professional photo of {business_name} services",
                                      "scheduled_time": f"{today} {pt['time']}:00",
                })

        return posts

    def _generate_post(
              self,
              business_context: str,
              platform_context: str,
              instruction: str,
              post_type: str
    ):
              """Call GPT-4o to generate caption + image prompt"""
              system_prompt = f"""You are an expert social media marketing copywriter specializing in local businesses.
      You write engaging, authentic posts that drive real engagement.
      {platform_context}

      IMPORTANT RULES:
      - Never use generic phrases like "We are excited to announce"
      - Write like a real local business owner, not a corporation
      - Include location vibes if relevant (community, neighborhood feel)
      - Vary the caption style each day - never repeat structures
      - Always make the CTA specific and actionable"""

        user_prompt = f"""BUSINESS CONTEXT:
        {business_context}

        TASK: {instruction}

        Return your response as JSON with exactly these two keys:
        {{
          "caption": "the full post caption text here",
            "image_prompt": "a detailed DALL-E prompt to generate a matching image (describe the scene, style, colors)"
}}"""

        response = self.client.chat.completions.create(
                      model=self.model,
                      messages=[
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": user_prompt}
                      ],
                      temperature=0.85,
                      response_format={"type": "json_object"},
                      max_tokens=600
        )

        result = json.loads(response.choices[0].message.content)
        caption = result.get("caption", "").strip()
        image_prompt = result.get("image_prompt", "").strip()
        return caption, image_prompt

    def _build_context(
              self, business_name: str, business_url: str, crawl_data: Optional[Dict]
    ) -> str:
              """Build a rich business context string from crawl data"""
              lines = [f"Business Name: {business_name}", f"Website: {business_url}"]

        if crawl_data:
                      if crawl_data.get("services"):
                                        lines.append(f"Services/Products: {', '.join(crawl_data['services'])}")
                                    if crawl_data.get("prices"):
                                                      lines.append(f"Sample Prices: {', '.join(crawl_data['prices'])}")
                                                  if crawl_data.get("key_headings"):
                                                                    lines.append(f"Key Page Headings: {', '.join(crawl_data['key_headings'][:5])}")
                                                                if crawl_data.get("image_descriptions"):
                                                                                  lines.append(f"Visual Content: {', '.join(crawl_data['image_descriptions'][:4])}")
                                                                              if crawl_data.get("text_samples"):
                                                                                                lines.append(f"About the business: {crawl_data['text_samples'][0][:300]}")

        return "\n".join(lines)

    def _platform_context(self, platform: str) -> str:
              """Platform-specific writing guidelines"""
        guidelines = {
                      "facebook": (
                                        "Platform: Facebook. Write for Facebook audiences (ages 25-55). "
                                        "Longer captions OK (up to 150 words). Use line breaks for readability. "
                                        "Tag location if known. Encourage comments and shares."
                      ),
                      "instagram": (
                                        "Platform: Instagram. Write for Instagram (ages 18-40). "
                                        "Punchy opening line. Use relevant hashtags at the end (5-8 hashtags). "
                                        "Keep main caption under 100 words, hashtags separate."
                      ),
                      "both": (
                                        "Platform: Facebook & Instagram cross-post. "
                                        "Write for broad audience. Include 3-5 hashtags inline. "
                                        "Engaging opening, clear CTA."
                      ),
        }
        return guidelines.get(platform.lower(), guidelines["facebook"])

    def _fallback_caption(self, business_name: str, post_type: str) -> str:
              """Emergency fallback captions if AI fails"""
        fallbacks = {
                      "morning_promo": (
                                        f"Good morning! Ready to make your event unforgettable? "
                                        f"{business_name} has everything you need. DM us to book today!"
                      ),
                      "afternoon_tip": (
                                        f"Planning a party? Here's a tip: always book your rentals "
                                        f"at least 2 weeks in advance for the best selection. "
                                        f"{business_name} has you covered!"
                      ),
                      "evening_proof": (
                                        f"Another amazing event in the books! Thank you to all our "
                                        f"customers who trusted {business_name} with their special day. "
                                        f"What's your favorite party memory? Tell us below!"
                      ),
        }
        return fallbacks.get(post_type, f"Check out {business_name} for all your event needs!")
