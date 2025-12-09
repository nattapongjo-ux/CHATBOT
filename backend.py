import google.generativeai as genai
import streamlit as st
import json
import io
import concurrent.futures
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# =================ตั้งค่า Config=================
MODEL_NAME = 'gemini-2.0-flash-lite' # หรือรุ่นที่คุณใช้
GENERATION_CONFIG = {
    "temperature": 0.3,
    "top_p": 0.8,
    "top_k": 40,
    "max_output_tokens": 8192,
}

def setup_api(api_key):
    genai.configure(api_key=api_key)

# =================ส่วนเชื่อมต่อ Google Drive=================
def get_drive_service():
    """สร้างการเชื่อมต่อโดยใช้ JSON Key จาก Secrets"""
    try:
        # อ่าน JSON จาก Secrets
        if "google_json" not in st.secrets:
            st.error("❌ ไม่พบ 'google_json' ใน Secrets")
            return None
            
        creds_info = json.loads(st.secrets["google_json"])
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ เชื่อมต่อ Drive ไม่สำเร็จ: {e}")
        return None

def _download_single_file(file_id, service, file_name):
    """ดาวน์โหลดเนื้อหาไฟล์ 1 ไฟล์ (ใช้สำหรับ Threading)"""
    try:
        request = service.files().get_media(fileId=file_id)
        file_io = io.BytesIO()
        downloader = MediaIoBaseDownload(file_io, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        
        content = file_io.getvalue().decode('utf-8')
        return f"--- File: {file_name} ---\n{content}\n"
    except Exception as e:
        return f"Error reading {file_name}: {e}\n"

def find_relevant_files(folder_id, user_query):
    """
    ค้นหาไฟล์ใน Google Drive (Search Logic)
    """
    service = get_drive_service()
    if not service: return []

    # 1. แปลงคำค้นหาเบื้องต้น
    keywords = user_query.split()
    query_parts = []
    
    # Logic: ค้นหาไฟล์ที่มีชื่อตรงกับคำใน Query (แบบคร่าวๆ)
    # หมายเหตุ: Drive API ค้นหาภาษาไทยอาจจะไม่แม่นยำเท่า os.walk
    # เราจึงดึงไฟล์ Text ทั้งหมดใน Folder มาก่อน แล้วค่อยกรองด้วย Python จะแม่นยำกว่าถ้าไฟล์ไม่เยอะมาก (หลักร้อยไฟล์)
    
    query = f"'{folder_id}' in parents and mimeType = 'text/plain' and trashed = false"
    
    try:
        # ดึงรายชื่อไฟล์ทั้งหมดใน Folder นั้น
        results = service.files().list(
            q=query,
            pageSize=50, # ดึงมาตรวจสอบ 50 ไฟล์
            fields="nextPageToken, files(id, name)"
        ).execute()
        files = results.get('files', [])
        
        relevant_files = []
        
        # กรองไฟล์ด้วย Python (แม่นยำกว่า Drive API)
        user_query_lower = user_query.lower()
        for f in files:
            # ถ้าชื่อไฟล์มีคำที่อยู่ในคำถาม หรือ คำถามมีชื่อไฟล์
            # หรือถ้าคำถามกว้างๆ ให้เอาไฟล์ที่มีคำว่า "สรุป" หรือ "รายงาน"
            f_name = f['name'].lower()
            if (f_name in user_query_lower) or \
               (any(k in f_name for k in keywords if len(k) > 2)) or \
               ("ทั้งหมด" in user_query_lower):
                
                relevant_files.append({"id": f['id'], "name": f['name']})
        
        # ถ้าหาเจาะจงไม่เจอเลย ให้เอาไฟล์ล่าสุด 5 ไฟล์ส่งไปแทน
        if not relevant_files and files:
            relevant_files = [{"id": f['id'], "name": f['name']} for f in files[:5]]
            
        return relevant_files

    except Exception as e:
        st.error(f"Error searching files: {e}")
        return []

# =================ส่วนคุยกับ Gemini=================
def ask_gemini_stream(file_list, question, timer_placeholder=None):
    """
    โหลดไฟล์คู่ขนาน -> ส่ง Prompt -> ตอบ Stream
    file_list: list ของ dict [{'id': '...', 'name': '...'}, ...]
    """
    service = get_drive_service()
    downloaded_texts = []
    total = len(file_list)
    
    # 1. Parallel Download (เร่งความเร็วการดึงข้อมูลจาก Drive)
    if total > 0:
        if timer_placeholder: 
            timer_placeholder.markdown(f"**⬇️ กำลังดาวน์โหลดข้อมูล {total} ไฟล์...**")
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_file = {
                executor.submit(_download_single_file, f['id'], service, f['name']): f 
                for f in file_list
            }
            
            for future in concurrent.futures.as_completed(future_to_file):
                result = future.result()
                downloaded_texts.append(result)
    else:
        yield "ไม่พบข้อมูลในระบบ Drive ครับ"
        return

    # 2. รวมข้อมูลเป็น Context (Text-based Context)
    full_context = "\n".join(downloaded_texts)
    
    # 3. สร้าง Prompt
    prompt = f"""
    Context: ข้อมูลเกษตรจากไฟล์เอกสารแนบ
    Task: ตอบคำถามโดยอ้างอิงข้อมูลด้านล่างนี้เท่านั้น
    
    ข้อมูลอ้างอิง:
    {full_context}
    
    คำถาม: {question}
    """
    
    # 4. เรียก Gemini (แบบ Text Prompt เพราะเราโหลดเนื้อหามาแล้ว)
    model = genai.GenerativeModel(MODEL_NAME)
    
    try:
        response = model.generate_content(
            prompt,
            stream=True,
            generation_config=GENERATION_CONFIG
        )
        
        for chunk in response:
            if chunk.text:
                yield chunk.text
                
    except Exception as e:
        yield f"⚠️ เกิดข้อผิดพลาด: {str(e)}"

def reply_general_chat(query):
    model = genai.GenerativeModel(MODEL_NAME)
    response = model.generate_content(query)
    return response.text
