import os
import time
import shutil
import datetime
import google.generativeai as genai
import concurrent.futures
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

# ================= Config =================
MODEL_NAME = 'gemini-2.5-flash-lite'
TEMP_DOWNLOAD_DIR = "temp_drive_files"  # โฟลเดอร์ชั่วคราวสำหรับพักไฟล์

# ตั้งค่า Safety Settings
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

GENERATION_CONFIG = {
    "temperature": 0.3,
    "top_p": 0.8,
    "top_k": 40,
    "max_output_tokens": 8192,
}

def setup_api(api_key):
    genai.configure(api_key=api_key)

# ================= Google Drive Logic =================

@st.cache_resource
def get_drive_service():
    """เชื่อมต่อ Google Drive API ครั้งเดียวแล้วจำไว้ (Cache)"""
    try:
        # ดึง Credential จาก st.secrets
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ เชื่อมต่อ Google Drive ไม่สำเร็จ: {e}")
        return None

def check_drive_folder_and_download(main_folder_id, user_prompt):
    """
    1. เช็คชื่อโฟลเดอร์ใน Drive (จังหวัด) ว่าตรงกับ user_prompt ไหม
    2. ถ้าตรง -> ดาวน์โหลดไฟล์ในนั้นมาลงเครื่อง Server ชั่วคราว
    3. คืนค่า list ของ path ไฟล์ที่โหลดมา
    """
    service = get_drive_service()
    if not service:
        return [], None

    # 1. ดึงรายชื่อโฟลเดอร์ลูก (จังหวัด) ในโฟลเดอร์หลัก
    query = f"'{main_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get('files', [])

    matched_folder = None
    matched_name = None

    # 2. วนลูปหาว่า Prompt มีชื่อจังหวัด(ชื่อโฟลเดอร์)ไหม
    for folder in folders:
        if folder['name'] in user_prompt: # เช่น user พิมพ์ "ตาก" เจอโฟลเดอร์ "ตาก"
            matched_folder = folder
            matched_name = folder['name']
            break
    
    downloaded_files = []

    # 3. ถ้าเจอจังหวัด ให้ดาวน์โหลดไฟล์
    if matched_folder:
        # เตรียมโฟลเดอร์ปลายทาง
        if os.path.exists(TEMP_DOWNLOAD_DIR):
            shutil.rmtree(TEMP_DOWNLOAD_DIR) # ล้างของเก่าทิ้ง
        os.makedirs(TEMP_DOWNLOAD_DIR)

        # ลิสต์ไฟล์ในโฟลเดอร์จังหวัดนั้น
        folder_id = matched_folder['id']
        file_query = f"'{folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
        file_results = service.files().list(q=file_query, fields="files(id, name, mimeType)").execute()
        files = file_results.get('files', [])

        # ดาวน์โหลดไฟล์ (เฉพาะ PDF, Excel, Text)
        allowed_exts = ['application/pdf', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'text/plain']
        
        status_text = st.empty()
        status_text.info(f"⬇️ กำลังดาวน์โหลดข้อมูลจาก Drive: {matched_name}...")

        for file in files:
            # เช็คสกุลไฟล์แบบคร่าวๆ หรือโหลดหมดก็ได้ถ้าไม่เยอะ
            # แต่เพื่อความชัวร์ Google Drive MIME types อาจจะซับซ้อน ให้ลองโหลดมาก่อน
            file_id = file['id']
            file_name = file['name']
            
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            # บันทึกลงเครื่อง Server (Temp)
            save_path = os.path.join(TEMP_DOWNLOAD_DIR, file_name)
            with open(save_path, 'wb') as f:
                f.write(fh.getbuffer())
            
            downloaded_files.append(save_path)
        
        status_text.empty() # ลบข้อความดาวน์โหลด
        
    return downloaded_files, matched_name

# ================= Gemini Logic (เหมือนเดิม) =================

@st.cache_resource(show_spinner=False, ttl=3600)
def _upload_single_cached(path):
    """อัปโหลดขึ้น Gemini (ตัด cache time ออกเพราะไฟล์เปลี่ยนชื่อเดิมตลอด)"""
    try:
        name = os.path.basename(path)
        uf = genai.upload_file(path=path, display_name=name)
        
        retry_count = 0
        while uf.state.name == "PROCESSING":
            time.sleep(1)
            uf = genai.get_file(uf.name)
            retry_count += 1
            if retry_count > 60: break
        return uf if uf.state.name != "FAILED" else None
    except Exception:
        return None

def ask_gemini_stream(file_paths, question, timer_placeholder=None, start_time=None):
    uploaded_files = []
    total = len(file_paths)
    
    # Upload Files to Gemini
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_upload_single_cached, path) for path in file_paths]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: uploaded_files.append(res)
    
    if not uploaded_files:
        yield "❌ ไม่สามารถอ่านไฟล์ได้เลยครับ (Upload Failed)"
        return

    # Prompt
    model = genai.GenerativeModel(MODEL_NAME)
    payload = uploaded_files + [
        f"""
        Role: Agricultural Data Specialist.
        Task: Answer based ONLY on the provided files.
        Question: "{question}"
        Rules: Use Thai language. State "ไม่พบข้อมูล" if missing.
        """
    ]

    try:
        response = model.generate_content(
            payload, 
            stream=True, 
            safety_settings=SAFETY_SETTINGS,
            generation_config=GENERATION_CONFIG
        )
        for chunk in response:
            if chunk.text:
                yield chunk.text
    except Exception as e:
        yield f"⚠️ Error: {str(e)}"

def reply_general_chat(user_query):
    # (ใช้ Logic เดิมได้เลย)
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(f"ตอบคำถามทั่วไปอย่างสุภาพ: {user_query}")
        return response.text
    except:
        return "ระบบขัดข้องชั่วคราว"
