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
    match_topic = re.search(r't\.me/(?:c/)?([^/]+)/(\d+)/(\d+)', url)
    if match_topic:
        return {
            'channel': match_topic.group(1),
            'topic_id': match_topic.group(2),
            'message_id': int(match_topic.group(3))
        }
    
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
    Strict block-level parser:
    - ONLY extracts links located strictly under 'Lectures' or 'Sections' blocks.
    - Completely ignores 'Recordings', 'HTML', Drive folders, and external links.
    """
    items = []
    if not text:
        return items

    # Split message into blocks separated by box headers ╭━━
    blocks = re.split(r'╭━━\s*', text)
    
    for block in blocks:
        lines = block.split('\n')
        header = lines[0].strip().lower()
        
        target_type = None
        if 'lecture' in header or 'محاضر' in header:
            target_type = 'Lecture'
        elif 'section' in header or 'سكاشن' in header or 'سيكشن' in header:
            target_type = 'Section'
        else:
            # Strictly skip any block that is NOT Lectures or Sections (e.g. HTML, Recordings, Books)
            continue
            
        # Check if block header specifies a student source (e.g. 'Lectures from student 1' or 'طالب 1')
        student_match = re.search(r'(?:student|طالب)\s*(\d+)', header)
        student_tag = f"(طالب {student_match.group(1)})" if student_match else None

        # Extract markdown links [label](url)
        md_matches = re.finditer(r'\[([^\]]+)\]\((https?://t\.me/[^\)]+)\)', block)
        for m in md_matches:
            raw_label = m.group(1)
            url = m.group(2)
            parsed_item = _process_block_link(raw_label, url, target_type, student_tag)
            if parsed_item:
                items.append(parsed_item)

    # Fallback only if no ╭━━ blocks exist at all
    if not items and '╭━━' not in text:
        md_matches = re.finditer(r'\[([^\]]+)\]\((https?://t\.me/[^\)]+)\)', text)
        for m in md_matches:
            raw_label = m.group(1)
            url = m.group(2)
            clean_label = re.sub(r'[*_~`\s]', '', normalize_text(raw_label)).upper()
            if re.match(r'^(?:L(?:EC)?|CH|محاضرة|محاضره)0*(\d+)$', clean_label):
                parsed = _process_block_link(raw_label, url, 'Lecture')
                if parsed: items.append(parsed)
            elif re.match(r'^(?:S(?:EC)?|LAB|LAP|سكشن|سيكشن)0*(\d+)$', clean_label):
                parsed = _process_block_link(raw_label, url, 'Section')
                if parsed: items.append(parsed)

    # Deduplicate items by message_id while preserving order
    unique_items = []
    seen_ids = set()
    for it in items:
        mid = it.get('message_id')
        if mid and mid not in seen_ids:
            seen_ids.add(mid)
            unique_items.append(it)

    return unique_items

def _process_block_link(raw_label: str, url: str, expected_type: str, student_tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
    norm = normalize_text(raw_label)
    norm_lower = norm.lower()

    # Reject unwanted content explicitly
    forbidden_terms = ['html', 'htm', 'تسجيل', 'صوت', 'ريكورد', 'recording', 'audio', 'ملخص', 'summary', 'اسئلة', 'أسئلة', 'quiz', 'bank', 'شيت', 'sheet', 'واجب']
    if any(ft in norm_lower for ft in forbidden_terms):
        return None

    clean_label = re.sub(r'[*_~`\s]', '', norm).upper()
    
    if expected_type == "Lecture":
        lec_match = re.match(r'^(?:L(?:EC)?|CH)0*(\d+)$', clean_label)
        ar_lec = re.search(r'(?:محاضرة|محاضره)\s*0*(\d+)', norm)
        week_match = re.search(r'(?:week|wk|w|اسبوع|الاسبوع|الأسبوع)\s*0*(\d+)', norm, re.IGNORECASE)
        if lec_match:
            num = int(lec_match.group(1))
            base_title = f"Lec {num:02d}"
        elif ar_lec:
            num = int(ar_lec.group(1))
            base_title = f"Lec {num:02d}"
        elif week_match:
            num = int(week_match.group(1))
            base_title = f"Lec {num:02d}"
        elif clean_label == "ALL":
            base_title = "Lec 01"
        else:
            base_title = re.sub(r'[*_~`]', '', norm).strip()

        title = f"{base_title} {student_tag}" if student_tag else base_title
        item_type = "Lecture"
    elif expected_type == "Section":
        sec_match = re.match(r'^(?:S(?:EC)?|LAB|LAP)0*(\d+)$', clean_label)
        ar_sec = re.search(r'(?:سكشن|سيكشن|لاب)\s*0*(\d+)', norm)
        week_match = re.search(r'(?:week|wk|w|اسبوع|الاسبوع|الأسبوع)\s*0*(\d+)', norm, re.IGNORECASE)
        if sec_match:
            num = int(sec_match.group(1))
            base_title = f"Sec {num:02d}"
        elif ar_sec:
            num = int(ar_sec.group(1))
            base_title = f"Sec {num:02d}"
        elif week_match:
            num = int(week_match.group(1))
            base_title = f"Sec {num:02d}"
        elif "ALL" in clean_label and ("LAP" in clean_label or "LAB" in clean_label or "SEC" in clean_label):
            base_title = "Sec 01"
        else:
            base_title = re.sub(r'[*_~`]', '', norm).strip()

        title = f"{base_title} {student_tag}" if student_tag else base_title
        item_type = "Section"
    else:
        return None

    # Clean any trailing markdown symbols
    title = re.sub(r'[*_~`]', '', title).strip()

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
