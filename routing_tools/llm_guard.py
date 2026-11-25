import hashlib
import json
from pathlib import Path

class LLMGuard:
    def __init__(self, history_dir=".routing_data/retry_history"):
        self.history_dir = Path(history_dir)
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass

    def get_history(self, story_id):
        history_file = self.history_dir / f"{story_id}.json"
        try:
            if history_file.exists():
                return json.loads(history_file.read_text())
        except (PermissionError, OSError):
            pass
        return {"story": story_id, "attempts": []}

    def save_history(self, history):
        history_file = self.history_dir / f"{history['story']}.json"
        try:
            history_file.write_text(json.dumps(history, indent=2))
        except (PermissionError, OSError):
            pass

    def calculate_hash(self, content):
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def check_loop(self, story_id, current_output):
        current_hash = self.calculate_hash(current_output)
        history = self.get_history(story_id)
        
        for attempt in history["attempts"]:
            if attempt.get("output_hash") == current_hash:
                return True
        return False

    def record_attempt(self, story_id, exit_code, output, tokens_used=0):
        history = self.get_history(story_id)
        current_hash = self.calculate_hash(output)
        
        history["attempts"].append({
            "attempt": len(history["attempts"]) + 1,
            "exit_code": exit_code,
            "output_hash": current_hash,
            "tokens": tokens_used
        })
        self.save_history(history)
        return len(history["attempts"])

    def get_total_tokens(self, story_id):
        history = self.get_history(story_id)
        return sum(a.get("tokens", 0) for a in history["attempts"])

    def check_budget(self, story_id, budget=100000):
        total = self.get_total_tokens(story_id)
        return total < budget, total
