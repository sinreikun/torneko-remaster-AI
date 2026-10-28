"""Stable serialization shared by perception identities without import cycles."""
import hashlib,json

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24]
