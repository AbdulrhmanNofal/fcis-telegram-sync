import os
import json
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger("FcisApi")

class FcisApiClient:
    def __init__(self, base_url: str, email: str, password: str, config_path: str = "config.json"):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.token: Optional[str] = None
        self.subjects_cache: Dict[str, int] = {}
        
        # Load topic mapping config
        self.config: Dict[str, Any] = {}
        resolved_config = config_path if os.path.isabs(config_path) else os.path.join(os.path.dirname(__file__), config_path)
        if os.path.exists(resolved_config):
            with open(resolved_config, "r", encoding="utf-8") as f:
                self.config = json.load(f)

    def login(self) -> bool:
        url = f"{self.base_url}/auth/login"
        payload = {"universityEmail": self.email, "password": self.password}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("data", {}).get("accessToken") or data.get("accessToken") or data.get("data", {}).get("token") or data.get("token")
                logger.info("Successfully authenticated with FCIS Hub API.")
                return True
            else:
                logger.error("Authentication failed: HTTP %s - %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("Error connecting to FCIS Hub API: %s", e)
            return False

    def _get_headers(self) -> Dict[str, str]:
        if not self.token:
            self.login()
        return {"Authorization": f"Bearer {self.token}"}

    def fetch_subjects(self):
        url = f"{self.base_url}/subjects"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for sub in data:
                    self.subjects_cache[sub["name"].strip().lower()] = sub["id"]
        except Exception as e:
            logger.warning("Could not fetch subjects: %s", e)

    def resolve_subject_id(self, topic_id: str, subject_name: Optional[str] = None) -> Optional[int]:
        # 1. Check topic mapping in config.json
        topic_cfg = self.config.get("topics", {}).get(str(topic_id))
        if topic_cfg and "subjectId" in topic_cfg:
            return topic_cfg["subjectId"]

        # 2. Check if name matches in database
        if subject_name and self.config.get("auto_resolve_by_name", True):
            if not self.subjects_cache:
                self.fetch_subjects()
            
            clean_name = subject_name.strip().lower()
            if clean_name in self.subjects_cache:
                return self.subjects_cache[clean_name]
            
            # Partial match search
            for name, sid in self.subjects_cache.items():
                if clean_name in name or name in clean_name:
                    return sid

        return None

    def get_existing_materials(self, subject_id: int) -> set:
        if not hasattr(self, "_existing_materials_cache"):
            self._existing_materials_cache = {}
            
        if subject_id in self._existing_materials_cache:
            return self._existing_materials_cache[subject_id]
            
        existing = set()
        try:
            url = f"{self.base_url}/materials?subjectId={subject_id}&pageSize=50"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                items = resp.json().get("data", {}).get("items", [])
                for item in items:
                    title = item.get("title", "").strip().lower()
                    m_type = item.get("type", "").strip().lower()
                    existing.add((title, m_type))
            self._existing_materials_cache[subject_id] = existing
        except Exception as e:
            logger.warning("Could not fetch existing materials for subject %s: %s", subject_id, e)
            
        return existing

    def is_material_already_on_platform(self, subject_id: int, title: str, material_type: str) -> bool:
        if not subject_id:
            return False
        existing = self.get_existing_materials(subject_id)
        key = (title.strip().lower(), material_type.strip().lower())
        return key in existing

    def upload_material(self, file_path: str, title: str, material_type: str, subject_id: int) -> Optional[Dict[str, Any]]:
        """
        Uploads a material file to POST /api/materials/upload
        """
        url = f"{self.base_url}/materials/upload"
        if not self.token and not self.login():
            return None

        file_name = os.path.basename(file_path)
        
        with open(file_path, "rb") as f:
            files = {
                "file": (file_name, f, "application/pdf")
            }
            data = {
                "Title": title,
                "Type": material_type,
                "SubjectId": subject_id
            }

            headers = self._get_headers()
            try:
                resp = requests.post(url, headers=headers, data=data, files=files, timeout=60)
                
                # If token expired, try to re-login once
                if resp.status_code == 401:
                    logger.info("Token expired, refreshing token...")
                    if self.login():
                        headers = self._get_headers()
                        f.seek(0)
                        resp = requests.post(url, headers=headers, data=data, files=files, timeout=60)

                if resp.status_code in [200, 201]:
                    logger.info("Uploaded successfully: %s -> %s", title, file_name)
                    return resp.json().get("data", {})
                else:
                    logger.error("Upload failed: HTTP %s - %s", resp.status_code, resp.text)
                    return None
            except Exception as e:
                logger.error("Exception during upload of %s: %s", title, e)
                return None
