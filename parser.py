import re
import unicodedata
from typing import List, Dict, Any, Optional

def normalize_text(text: str) -> str:
    """
    Normalizes Unicode characters (e.g., Mathematical Bold/Italic 𝑳𝟎𝟏 -> L01)
    """
    if not text:
        return ""
    return unicodedata.normalize('NFKC', text).strip()

def extract_subject_info(text: str) -> Dict[str, Optional[str]]:
    """
    Extracts subject name and code from the message header.
    Example: 'رسالة مجمعة لكل ما يخص مادة Web Development (WEB)'
    Returns: {'name': 'Web Development', 'code': 'WEB'}
    """
    normalized = normalize_text(text)
    
    # Try pattern: مادة <Name> (<Code>)
    match = re.search(r'مادة\s+([^(]+?)(?:\s*\(([^)]+)\))?(?:\n|$)', normalized)
    if match:
        name = match.group(1).strip()
        code = match.group(2).strip() if match.group(2) else None
        return {'name': name, 'code': code}
    
    return {'name': None, 'code': None}

def parse_telegram_url(url: str) -> Optional[Dict[str, Any]]:
    """
    Parses a Telegram message link:
    e.g., https://t.me/FCISCommunity29/17/111 -> topic_id: '17', message_id: 111
    or https://t.me/c/123456789/111 -> message_id: 111
    """
    # Pattern with topic: t.me/<channel_or_c>/<topic_id>/<message_id>
    match_topic = re.search(r't\.me/(?:c/)?([^/]+)/(\d+)/(\d+)', url)
    if match_topic:
        return {
            'channel': match_topic.group(1),
            'topic_id': match_topic.group(2),
            'message_id': int(match_topic.group(3))
        }
    
    # Simple pattern: t.me/<channel_or_c>/<message_id>
    match_simple = re.search(r't\.me/(?:c/)?([^/]+)/(\d+)', url)
    if match_simple:
        return {
            'channel': match_simple.group(1),
            'topic_id': None,
            'message_id': int(match_simple.group(2))
        }
    
    return None

def parse_overview_message(text: str, entities: list = None) -> List[Dict[str, Any]]:
    """
    Parses the overview message and returns a list of items to sync.
    Each item contains:
    - code: e.g. 'L01'
    - title: e.g. 'Lec 01' or 'Sec 01'
    - type: 'Lecture' | 'Section' | 'Summary'
    - url: Telegram link
    - message_id: int
    - topic_id: str
    """
    items = []
    
    # 1. First attempt: Parse markdown links [text](url)
    md_matches = re.finditer(r'\[([^\]]+)\]\((https?://t\.me/[^\)]+)\)', text)
    for m in md_matches:
        raw_label = m.group(1)
        url = m.group(2)
        parsed_item = _process_link(raw_label, url)
        if parsed_item:
            items.append(parsed_item)
            
    # 2. If no markdown links found but entities provided (Telethon MessageEntityTextUrl)
    if not items and entities:
        for ent in entities:
            # Telethon entity check
            if hasattr(ent, 'url') and ent.url and 't.me/' in ent.url:
                # Extract text slice for this entity
                raw_label = text[ent.offset : ent.offset + ent.length]
                parsed_item = _process_link(raw_label, ent.url)
                if parsed_item:
                    items.append(parsed_item)
                    
    return items

def _process_link(raw_label: str, url: str) -> Optional[Dict[str, Any]]:
    clean_label = re.sub(r'[*_~`\s]', '', normalize_text(raw_label)).upper()
    
    # Check if this is a lecture (L01, L02, LEC 1, CH 1, etc.)
    lec_match = re.match(r'^(?:L(?:EC)?|CH)0*(\d+)$', clean_label)
    sec_match = re.match(r'^(?:S(?:EC)?|LAB|H)0*(\d+)$', clean_label)
    
    if lec_match:
        num = int(lec_match.group(1))
        item_type = "Lecture"
        title = f"Lec {num:02d}"
    elif sec_match:
        num = int(sec_match.group(1))
        item_type = "Section"
        title = f"Sec {num:02d}"
    else:
        # Ignore non-lecture/non-section links (like bot deep links or other formats for now)
        return None

    tg_info = parse_telegram_url(url)
    if not tg_info or not tg_info['message_id']:
        return None

    return {
        'raw_label': raw_label,
        'normalized_code': clean_label,
        'title': title,
        'type': item_type,
        'url': url,
        'topic_id': tg_info.get('topic_id') or "general",
        'message_id': tg_info['message_id']
    }
