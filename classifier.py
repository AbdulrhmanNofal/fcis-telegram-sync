import os
import re
import json
import logging
import unicodedata
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("MaterialClassifier")

# ONLY allowed extensions: PDF and PowerPoint
ALLOWED_EXTENSIONS = {'.pdf', '.pptx', '.ppt'}

# STRICT rejection keywords for non-lecture / non-section content
REJECT_KEYWORDS = [
    # Summaries & reviews
    'ملخص', 'ملخصات', 'تلخيص', 'summary', 'summaries', 'cheatsheet', 'cheat sheet', 
    'مراجعة', 'مراجعه', 'review', 'revision', 'كشكول', 'دفتر',
    # Exams, quizzes & questions
    'امتحان', 'امتحانات', 'فاينل', 'فاينال', 'ميد', 'ميدترم', 'mid', 'final', 
    'exam', 'exams', 'quiz', 'quizzes', 'test', 'tests', 'اسئلة', 'أسئلة', 
    'سؤال', 'questions', 'bank', 'بنك', 'question',
    # Assignments & sheets
    'شيت', 'شيتات', 'sheet', 'sheets', 'واجب', 'واجبات', 'تكليف', 'تكليفات',
    'assignment', 'assignments', 'homework', 'hw', 'task', 'tasks', 'حلول', 
    'solutions', 'حل', 'answer', 'answers',
    # Audio / video / web files
    'تسجيل', 'تسجيلات', 'صوت', 'ريكورد', 'recording', 'recordings', 'audio', 
    'html', 'htm', 'link'
]

# Subject aliases for auto-mapping
SUBJECT_ALIASES = {
    1: {  # Data Structures & Algorithms
        'canonical': 'Data-Structures & Algorithms',
        'code': 'DSA',
        'keywords': ['data structure', 'data structures', 'dsa', 'خوارزميات', 'هياكل بيانات', 'خوارزمية', 'algorithm', 'algorithms']
    },
    2: {  # Computer Organization & Architecture
        'canonical': 'Computer Organization & Architecture',
        'code': 'Arc & Org',
        'keywords': ['arc & org', 'architecture', 'organization', 'معمارية', 'تنظيم', 'معمارية الحاسب', 'esraa salah', 'esraa-salah', 'arc_org', 'cse102']
    },
    3: {  # Operating Systems
        'canonical': 'Operating Systems',
        'code': 'OS',
        'keywords': ['operating systems', 'operating system', 'نظم التشغيل', 'نظام تشغيل', ' os ', 'os_', 'os ']
    },
    4: {  # Web Development
        'canonical': 'Web Development',
        'code': 'WEB',
        'keywords': ['web development', 'web programming', 'تطوير الويب', 'تطبيقات الويب', 'ويب', 'html', 'css', 'javascript', 'web_']
    },
    5: {  # Linear Algebra
        'canonical': 'Linear Algebra',
        'code': 'LA',
        'keywords': ['linear algebra', 'linear', 'algebra', 'جبر خطي', 'الجبر الخطي', 'جبر', 'adel', 'mohamed adel']
    }
}

def normalize_arabic_text(text: str) -> str:
    """Standardizes arabic characters and unicode representations."""
    if not text:
        return ""
    norm = unicodedata.normalize('NFKC', text)
    norm = re.sub(r'[إأآا]', 'ا', norm)
    norm = re.sub(r'ة', 'ه', norm)
    norm = re.sub(r'ى', 'ي', norm)
    norm = re.sub(r'[\u064B-\u065F\u0670]', '', norm)  # Remove harakat
    return norm.lower().strip()

def is_strictly_rejected(combined_text: str) -> Tuple[bool, Optional[str]]:
    """Checks if text contains forbidden content (summaries, exams, sheets, assignments)."""
    norm = normalize_arabic_text(combined_text)
    
    # Check rejection keywords using word-boundary or substring where appropriate
    for kw in REJECT_KEYWORDS:
        norm_kw = normalize_arabic_text(kw)
        pattern = r'(?:^|\s|[_\-.,()\[\]])' + re.escape(norm_kw) + r'(?:$|\s|[_\-.,()\[\]])'
        if re.search(pattern, norm) or (len(norm_kw) > 4 and norm_kw in norm):
            return True, f"محتوى غير مسموح به ({kw}): يسمح فقط بالمحاضرات والسكاشن الرسمية."
            
    return False, None

def resolve_subject_from_text(combined_text: str, topic_id: str = "", config_topics: dict = None) -> Tuple[Optional[int], Optional[str]]:
    """Resolves subject ID and name from topic_id or keywords in text."""
    # 1. Match by Telegram topic_id if known
    if config_topics and str(topic_id) in config_topics:
        t_info = config_topics[str(topic_id)]
        return t_info.get("subjectId"), t_info.get("englishName") or t_info.get("subjectName")

    norm = normalize_arabic_text(combined_text)

    # 2. Match by keyword aliases
    for s_id, s_info in SUBJECT_ALIASES.items():
        for kw in s_info['keywords']:
            norm_kw = normalize_arabic_text(kw)
            if norm_kw in norm:
                return s_id, s_info['canonical']

    return None, None

def extract_contributor_tag(text: str) -> str:
    """Extracts student/contributor distinction (e.g., طالب 1, طالب 2, د. فلان)."""
    stu_m = re.search(r'(?:student|stu|طالب|no)\s*0*(\d+)', text, re.IGNORECASE)
    if stu_m:
        return f"(طالب {stu_m.group(1)})"
        
    doc_m = re.search(r'(?:دكتور|د\.|dr\.)\s*([^\s,\-_()]+)', text, re.IGNORECASE)
    if doc_m:
        return f"(د. {doc_m.group(1)})"
        
    eng_m = re.search(r'(?:مهندس|مهندسة|م\.|eng\.)\s*([^\s,\-_()]+)', text, re.IGNORECASE)
    if eng_m:
        return f"(م. {eng_m.group(1)})"

    return ""

def rule_classify(file_name: str, message_text: str = "", topic_id: str = "", config_topics: dict = None, expected_type: Optional[str] = None) -> Dict[str, Any]:
    """
    High-precision rule-based classifier:
    - Enforces .pdf, .pptx, .ppt
    - Enforces Lecture or Section ONLY
    - Standardizes title to 'Lec 01', 'Sec 02', etc.
    """
    base_name, ext = os.path.splitext(file_name)
    ext = ext.lower()

    if ext not in ALLOWED_EXTENSIONS:
        return {
            "isValid": False,
            "rejectionReason": f"امتداد الملف غير مدعوم ({ext}). يُسمح بملفات PDF و PowerPoint فقط.",
            "title": "",
            "type": "",
            "subjectId": None,
            "subjectName": "",
            "confidence": "rule_reject"
        }

    combined = f"{base_name} {message_text}"
    
    # Check rejection filters (summaries, exams, questions, sheets, assignments)
    rejected, reason = is_strictly_rejected(combined)
    if rejected:
        return {
            "isValid": False,
            "rejectionReason": reason,
            "title": "",
            "type": "",
            "subjectId": None,
            "subjectName": "",
            "confidence": "rule_reject"
        }

    # Identify Type and Number
    norm = normalize_arabic_text(combined)

    # Patterns for Lectures
    lec_pattern = re.compile(
        r'(?:lec(?:ture)?|ch(?:apter)?|محاضر[ةه]|شابتر|فصل|سلايد(?:ات)?)\s*[-_.]?\s*0*(\d+)',
        re.IGNORECASE
    )
    # Patterns for Sections
    sec_pattern = re.compile(
        r'(?:sec(?:tion)?|lab|lap|سكشن|سيكشن|لاب|معمل)\s*[-_.]?\s*0*(\d+)',
        re.IGNORECASE
    )

    lec_m = lec_pattern.search(norm)
    sec_m = sec_pattern.search(norm)

    item_type = None
    item_num = None

    if lec_m and not sec_m:
        item_type = "Lecture"
        item_num = int(lec_m.group(1))
    elif sec_m and not lec_m:
        item_type = "Section"
        item_num = int(sec_m.group(1))
    elif lec_m and sec_m:
        fn_norm = normalize_arabic_text(base_name)
        if lec_pattern.search(fn_norm):
            item_type = "Lecture"
            item_num = int(lec_pattern.search(fn_norm).group(1))
        elif sec_pattern.search(fn_norm):
            item_type = "Section"
            item_num = int(sec_pattern.search(fn_norm).group(1))
        else:
            item_type = "Lecture"
            item_num = int(lec_m.group(1))
    else:
        # Check underscore/hyphen numbering e.g. "Web Programming _01" or "DSA _02"
        num_suffix = re.search(r'[_\-\s]0*(\d+)(?:\.|$)', base_name)
        if expected_type in ("Lecture", "Section") and num_suffix:
            item_type = expected_type
            item_num = int(num_suffix.group(1))
        elif expected_type in ("Lecture", "Section"):
            item_type = expected_type
            item_num = 1
        elif num_suffix:
            # If no sec/lab keywords exist, default numeric slide sequence to Lecture
            item_type = "Lecture"
            item_num = int(num_suffix.group(1))
        elif any(w in norm for w in ['محاضره', 'محاضرة', 'lecture', 'slides']):
            item_type = "Lecture"
            item_num = 1
        elif any(w in norm for w in ['سكشن', 'سيكشن', 'section', 'lab manual', 'معمل']):
            item_type = "Section"
            item_num = 1
        else:
            return {
                "isValid": False,
                "rejectionReason": "لم يتم التعرف على الملف كمحاضرة أو سكشن دراسي.",
                "title": "",
                "type": "",
                "subjectId": None,
                "subjectName": "",
                "confidence": "rule_reject"
            }

    # Build standardized title
    prefix = "Lec" if item_type == "Lecture" else "Sec"
    clean_title = f"{prefix} {item_num:02d}"

    contributor = extract_contributor_tag(combined)
    if contributor:
        clean_title = f"{clean_title} {contributor}"

    # Resolve Subject
    s_id, s_name = resolve_subject_from_text(combined, topic_id, config_topics)

    return {
        "isValid": True,
        "rejectionReason": None,
        "title": clean_title,
        "type": item_type,
        "subjectId": s_id,
        "subjectName": s_name or (f"Topic {topic_id}" if topic_id else "عام"),
        "confidence": "rule_high"
    }

class AiClassifierAgent:
    """
    AI Agent that uses Google Gemini API to classify Telegram materials with deep context understanding.
    Activated whenever GEMINI_API_KEY is present in the environment.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 10)

    def classify_with_gemini(self, file_name: str, message_text: str = "", topic_id: str = "", subjects_list: list = None) -> Optional[Dict[str, Any]]:
        if not self.is_available():
            return None

        import requests

        subjects_desc = json.dumps(subjects_list or [
            {"id": 1, "name": "Data-Structures & Algorithms", "code": "DSA"},
            {"id": 2, "name": "Computer Organization & Architecture", "code": "Arc & Org"},
            {"id": 3, "name": "Operating Systems", "code": "OS"},
            {"id": 4, "name": "Web Development", "code": "WEB"},
            {"id": 5, "name": "Linear Algebra", "code": "LA"},
        ], ensure_ascii=False)

        prompt = f"""You are an expert AI Classifier Agent for the university academic hub 'FCIS Hub'.
Your job is to classify university Telegram materials into their correct academic subject, type, and standardized title.

CRITICAL RULES:
1. ONLY ACCEPT official Lectures ('Lecture') and practical Sections ('Section').
2. STRICTLY REJECT anything else:
   - Summaries, cheatsheets, notes (ملخصات, مراجعات) -> REJECT
   - Question banks, exams, quizzes, midterms, finals (أسئلة, بنك, امتحانات) -> REJECT
   - Sheets, homework, assignments, tasks, solutions (شيتات, واجبات, حلول) -> REJECT
   - HTML, recordings, audio files -> REJECT
3. ALLOWED FILE EXTENSIONS: .pdf, .pptx, .ppt ONLY. Any other extension -> REJECT.
4. Standardized Title Format:
   - Lectures: 'Lec 01', 'Lec 02', etc.
   - Sections: 'Sec 01', 'Sec 02', etc.
   - If there is a student or professor distinction (e.g. طالب 1 or د. فلان), append it: 'Lec 01 (طالب 1)'.

INPUT DATA:
- File Name: "{file_name}"
- Message Caption / Text: "{message_text}"
- Topic ID: "{topic_id}"
- Available Subjects: {subjects_desc}

OUTPUT REQUIREMENTS:
Return ONLY a valid JSON object matching this schema with no markdown code blocks:
{{
  "isValid": true/false,
  "rejectionReason": "reason if rejected or null",
  "type": "Lecture" | "Section" | null,
  "title": "Lec 01" | "Sec 02" | "",
  "subjectId": <int id from subjects list or null>,
  "subjectName": "<subject english name or null>",
  "confidence": "ai_gemini"
}}
"""

        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json"
                }
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data['candidates'][0]['content']['parts'][0]['text']
                res_obj = json.loads(raw_text)
                return res_obj
            else:
                logger.warning("Gemini AI API returned status %s: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.warning("Gemini AI Agent classification failed or timed out: %s", e)

        return None

def classify_telegram_item(file_name: str, message_text: str = "", topic_id: str = "", config_topics: dict = None, subjects_list: list = None, expected_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Unified Classifier function:
    1. Checks extension and obvious rejections instantly.
    2. Runs rule-based classification.
    3. If rule-based is ambiguous, tries AI Agent (Gemini) if configured.
    """
    _, ext = os.path.splitext(file_name)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        return {
            "isValid": False,
            "rejectionReason": f"امتداد الملف ({ext}) غير مسموح به. يُسمح فقط بـ PDF و PowerPoint.",
            "title": "",
            "type": "",
            "subjectId": None,
            "subjectName": "",
            "confidence": "rule_reject"
        }

    # Run Rule-Based Classifier
    result = rule_classify(file_name, message_text, topic_id, config_topics, expected_type=expected_type)

    # If rule-based classified cleanly, return it
    if result["isValid"] and result["subjectId"] and result["title"]:
        return result

    # If result was rejected due to strictly forbidden keywords (summary, exam, etc.), respect it immediately
    if not result["isValid"] and result["confidence"] == "rule_reject":
        return result

    # Ambiguous case: Try AI Agent if available
    ai_agent = AiClassifierAgent()
    if ai_agent.is_available():
        ai_res = ai_agent.classify_with_gemini(file_name, message_text, topic_id, subjects_list)
        if ai_res and "isValid" in ai_res:
            return ai_res

    return result
