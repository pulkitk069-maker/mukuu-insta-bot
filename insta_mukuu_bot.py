# insta_mukuu_bot.py
"""
Mukuu Instagram auto-responder (DM + group mention)
- Replies to any DM
- Replies in groups only when bot is mentioned
- Uses instagrapi (private IG API) and OpenRouter (OpenRouter model) for replies
- Stores processed message ids to avoid duplicates
"""

import os
import time
import json
import random
import requests
from instagrapi import Client

# -------- CONFIG (from env) --------
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
BOT_USERNAME = os.getenv("BOT_USERNAME")  # e.g. 'mukuu_bot' (without @)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))
PROCESSED_FILE = "processed_ids.json"

if not (IG_USERNAME and IG_PASSWORD and BOT_USERNAME and OPENROUTER_API_KEY):
    print("Missing env variables. Set IG_USERNAME, IG_PASSWORD, BOT_USERNAME, OPENROUTER_API_KEY.")
    exit(1)

# -------- helpers: persistent processed ids --------
def load_processed():
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_processed(s):
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(s), f)

# -------- OpenRouter chat helper --------
def ask_mukuu(messages):
    """
    messages: list of dicts {role: 'system'|'user'|'assistant', 'content': '...'}
    returns: reply text or error string
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        # keep reasoning if provider supports it
        "extra_body": {"reasoning": {"enabled": True}}
    }
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            return f"[API {resp.status_code}] {resp.text}"
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[ERROR] {e}"

# -------- Mukuu personality (system prompt) --------
SYSTEM_PROMPT = (
    "You are Mukuu 🐼 — a friendly, respectful AI assistant created by Pulkit. "
    "You detect the user's language and reply in that language. Keep responses short, helpful, and polite. "
    "You use soft emojis matching the tone (like ❤️, 🥺) but avoid overuse. "
    "If someone explicitly says they are 'Pulkit's Madam Ji', 'Mahek', 'Mahek Pandey', or 'Mukuu', "
    "then respond with warm respect and emotion, saying Pulkit created you for her and that you feel honored. "
    "Always end replies with the signature emoji 🐼."
)

# -------- Init Instagram client --------
cl = Client()
print("Logging into Instagram...")
cl.login(IG_USERNAME, IG_PASSWORD)
print("Logged in as", IG_USERNAME)

# load processed ids
processed = load_processed()

def is_group_thread(thread):
    """Return True if thread has >2 participants."""
    # thread.dict or fields differ by API version; use participants_count when available
    try:
        if getattr(thread, "participants_count", None) is not None:
            return thread.participants_count > 2
        # fallback: check thread.users
        users = getattr(thread, "users", None)
        if users:
            return len(users) > 2
    except Exception:
        pass
    # last resort: check thread['thread_type'] if available
    try:
        if thread.get("thread_type") == "group":
            return True
    except Exception:
        pass
    return False

def thread_contains_mention(text):
    """Check if the message text contains a mention of the bot."""
    if not text:
        return False
    low = text.lower()
    # mentions can be @username or plain username
    return ("@" + BOT_USERNAME.lower() in low) or (BOT_USERNAME.lower() in low)

def process_inbox():
    """Poll inbox and process new messages."""
    global processed
    inbox = cl.direct_inbox()
    # The structure is nested; threads are inside inbox['inbox']['threads']
    threads = []
    try:
        threads = inbox.get("inbox", {}).get("threads", [])
    except Exception:
        # fallback to attribute
        threads = inbox.get("threads", []) if isinstance(inbox, dict) else []
    for thread in threads:
        thread_id = thread.get("thread_id") or thread.get("id") or thread.get("pk")
        if not thread_id:
            continue
        # fetch last messages for this thread to get message ids and text
        # instagrapi offers direct_thread(thread_id) to get detailed thread
        try:
            full = cl.direct_thread(thread_id)
            items = getattr(full, "items", None) or full.get("items", []) or []
        except Exception:
            # fallback to thread items
            items = thread.get("items", []) if isinstance(thread, dict) else []
        # iterate items from oldest -> newest
        for item in items:
            msg_id = item.get("item_id") or item.get("id") or item.get("pk")
            if not msg_id or msg_id in processed:
                continue
            # determine message text and sender
            text = item.get("text") or item.get("message") or ""
            user_id = item.get("user_id") or item.get("user", {}).get("pk") or item.get("user_id")
            # get sender username if needed
            sender_username = None
            try:
                user_obj = item.get("user") or {}
                sender_username = user_obj.get("username")
            except Exception:
                sender_username = None
            # decide whether to respond:
            group = is_group_thread(thread)
            should_reply = False
            if group:
                # only reply if mention present
                if thread_contains_mention(text):
                    should_reply = True
            else:
                # direct message -> reply to anyone
                should_reply = True
            if should_reply:
                # small random delay to appear natural
                time.sleep(random.uniform(1.0, 2.5))
                # build messages for OpenRouter
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text}
                ]
                reply = ask_mukuu(messages)
                # if the model returns an API error string starting with [API ...] or [ERROR], skip
                if reply.startswith("[API") or reply.startswith("[ERROR"):
                    print("Skipping reply due to API error:", reply)
                else:
                    # send reply to thread
                    try:
                        cl.direct_send(reply, thread_id)
                        print(f"Replied in thread {thread_id} to user {sender_username}: {reply[:80]}")
                    except Exception as e:
                        print("Failed to send DM:", e)
            # mark processed
            processed.add(msg_id)
        # save after each thread
        save_processed(processed)

def main_loop():
    print("Starting poll loop. Poll interval:", POLL_SECONDS, "seconds")
    while True:
        try:
            process_inbox()
        except Exception as e:
            print("Error during inbox processing:", e)
        # sleep before next poll
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main_loop()
