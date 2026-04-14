"""
FastPost Social v3 - Flask Backend Server
Main API server for the AI Social Media Marketing Bot
"""

import os
import json
from flask import Flask, request, jsonify, session
from flask_cors import CORS
from dotenv import load_dotenv
from database import Database
from crawler import BusinessCrawler
from ai_generator import AIContentGenerator
from scheduler import PostScheduler
from stealth_poster import StealthPoster

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fastpost-secret-key-change-in-production")
CORS(app, supports_credentials=True)

# Initialize core components
db = Database()
crawler = BusinessCrawler()
ai_gen = AIContentGenerator(api_key=os.getenv("OPENAI_API_KEY"))
scheduler = PostScheduler(db=db, ai_gen=ai_gen)
poster = StealthPoster(db=db)

# ─── ACCOUNT ROUTES ──────────────────────────────────────────────────────────

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
      """Return all linked social media accounts"""
      accounts = db.get_all_accounts()
      return jsonify({"accounts": accounts})

@app.route("/api/accounts", methods=["POST"])
def add_account():
      """Link a new social media account"""
      data = request.json
      required = ["platform", "page_url", "business_url", "business_name"]
      if not all(k in data for k in required):
        return jsonify({"error": "Missing required fields"}), 400

    account_id = db.add_account(
              platform=data["platform"],
              page_url=data["page_url"],
              business_url=data["business_url"],
              business_name=data["business_name"],
              session_data=None
    )
    # Trigger initial crawl
    crawl_result = crawler.crawl(data["business_url"])
    db.update_crawl_data(account_id, crawl_result)

    return jsonify({"id": account_id, "message": "Account linked and site crawled"}), 201

@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
      """Remove a linked account"""
      db.delete_account(account_id)
      return jsonify({"message": "Account removed"})

# ─── CRAWL ROUTES ────────────────────────────────────────────────────────────

@app.route("/api/crawl/<int:account_id>", methods=["POST"])
def recrawl(account_id):
      """Re-crawl the business website to refresh content"""
      account = db.get_account(account_id)
      if not account:
                return jsonify({"error": "Account not found"}), 404

      result = crawler.crawl(account["business_url"])
      db.update_crawl_data(account_id, result)
      return jsonify({"message": "Crawl complete", "pages_found": result.get("pages_count", 0)})

# ─── POST QUEUE ROUTES ───────────────────────────────────────────────────────

@app.route("/api/queue", methods=["GET"])
def get_queue():
      """Return today's post queue across all accounts"""
      posts = db.get_todays_queue()
      return jsonify({"posts": posts})

@app.route("/api/queue/generate", methods=["POST"])
def generate_posts():
      """Generate AI posts for all accounts for today"""
      data = request.json or {}
      account_id = data.get("account_id")  # Optional: generate for specific account

    if account_id:
              accounts = [db.get_account(account_id)]
else:
          accounts = db.get_all_accounts()

    generated = []
    for account in accounts:
              if not account:
                            continue
                        crawl_data = db.get_crawl_data(account["id"])
        posts = ai_gen.generate_daily_posts(
                      business_name=account["business_name"],
                      business_url=account["business_url"],
                      platform=account["platform"],
                      crawl_data=crawl_data
        )
        for post in posts:
                      post_id = db.add_post(
                                        account_id=account["id"],
                                        caption=post["caption"],
                                        post_type=post["type"],
                                        scheduled_time=post["scheduled_time"],
                                        image_prompt=post.get("image_prompt", "")
                      )
                      generated.append({"id": post_id, "type": post["type"], "account": account["business_name"]})

    return jsonify({"generated": generated, "count": len(generated)})

@app.route("/api/queue/<int:post_id>", methods=["GET"])
def get_post(post_id):
      """Get a specific post from the queue"""
    post = db.get_post(post_id)
    if not post:
              return jsonify({"error": "Post not found"}), 404
    return jsonify(post)

@app.route("/api/queue/<int:post_id>", methods=["PUT"])
def update_post(post_id):
      """Edit a post caption before publishing"""
    data = request.json
    db.update_post_caption(post_id, data.get("caption", ""))
    return jsonify({"message": "Post updated"})

@app.route("/api/queue/<int:post_id>", methods=["DELETE"])
def delete_post(post_id):
      """Delete a post from the queue"""
    db.delete_post(post_id)
    return jsonify({"message": "Post deleted"})

# ─── POSTING ROUTES ──────────────────────────────────────────────────────────

@app.route("/api/post/<int:post_id>", methods=["POST"])
def post_now(post_id):
      """
          Human-in-the-Loop: User triggered this.
              Launches stealth browser to post to social media.
                  """
    post = db.get_post(post_id)
    if not post:
              return jsonify({"error": "Post not found"}), 404

    account = db.get_account(post["account_id"])
    if not account:
              return jsonify({"error": "Account not found"}), 404

    result = poster.post(
              platform=account["platform"],
        page_url=account["page_url"],
              caption=post["caption"],
              account_id=account["id"]
    )

    if result["success"]:
              db.mark_post_published(post_id)
        return jsonify({"message": "Posted successfully", "post_id": post_id})
else:
        return jsonify({"error": result.get("error", "Unknown error")}), 500

# ─── ANALYTICS ROUTES ────────────────────────────────────────────────────────

@app.route("/api/analytics", methods=["GET"])
def get_analytics():
      """Return post performance analytics"""
    stats = db.get_analytics()
    return jsonify(stats)

@app.route("/api/analytics/<int:account_id>", methods=["GET"])
def get_account_analytics(account_id):
      """Return analytics for a specific account"""
    stats = db.get_account_analytics(account_id)
    return jsonify(stats)

# ─── SCHEDULER ROUTES ────────────────────────────────────────────────────────

@app.route("/api/scheduler/start", methods=["POST"])
def start_scheduler():
      """Start the automatic post generation scheduler"""
    scheduler.start()
    return jsonify({"message": "Scheduler started"})

@app.route("/api/scheduler/stop", methods=["POST"])
def stop_scheduler():
      """Stop the scheduler"""
    scheduler.stop()
    return jsonify({"message": "Scheduler stopped"})

@app.route("/api/scheduler/status", methods=["GET"])
def scheduler_status():
      """Get scheduler status"""
    return jsonify({"running": scheduler.is_running(), "next_run": scheduler.next_run()})

# ─── HEALTH CHECK ────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
      return jsonify({"status": "ok", "version": "3.0.0"})

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
      db.init_db()
    scheduler.start()
    app.run(debug=True, host="0.0.0.0", port=5000)
