import google.generativeai as genai
import streamlit as st
import json
import io
import concurrent.futures
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ================= Config =================
MODEL_NAME = 'gemini-2.0-flash-lite'
GENERATION_CONFIG = {
    "temperature": 0.3,
    "top_p": 0.8,
    "top_k": 40,
    "max_output_tokens": 8192,
}

def setup_api(api_key):
    genai.configure(api_key=api_key)

# ================= Drive Connection =================
def get_drive_service():
    try:
        if "google_json" not in st.secrets:
            st.error("❌ ไม่พบ 'google_json' ใน Secrets")
            return None
        creds_info = json.loads(st.secrets["google_json"])
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ เชื่อมต่อ Drive ไม่สำเร็จ: {e}")
        return None

def _download_single_file(file_id, service, file_name):
    """ดาวน์โหลดไฟล์ 1 ไฟล์"""
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

# ================= ✨ ฟังก์ชันใหม่: สร้างแผนที่ชื่อจังหวัด -> ID =================
@st.cache_data(ttl=3600) # จำค่าไว้ 1 ชั่วโมง จะได้ไม่ต้องโหลดบ่อยๆ
def get_province_map(root_folder_id):
    """
    ไปสแกน Root Folder แล้วสร้างคู่มือ: {'เชียงราย': 'ID_1', 'น่าน': 'ID_2'}
    """
    service = get_drive_service()
    if not service: return {}
    
    try:
        # หา Folder ทั้งหมดที่อยู่ใน Root
        query = f"'{root_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(
            q=query, pageSize=100, fields="files(id, name)"
        ).execute()
        
        folders = results.get('files', [])
        
        # สร้าง Dictionary เก็บค่า {'ชื่อจังหวัด': 'ID'}
        # ตัดช่องว่างออกเพื่อให้หาง่าย (เช่น " เชียงราย " -> "เชียงราย")
        province_map = {f['name'].strip(): f['id'] for f in folders}
        return province_map
        
    except Exception as e:
        print(f"Error mapping provinces: {e}")
        return {}

# ================= Logic ค้นหาแบบฉลาด (Smart Router) =================
def find_relevant_files(root_folder_id, user_query):
    service = get_drive_service()
    if not service: return []

    # 1. โหลดแผนที่จังหวัดมาก่อน (เชียงราย=IDอะไร, น่าน=IDอะไร)
    province_map = get_province_map(root_folder_id)
    
    # 2. ตรวจสอบว่าในคำถาม มีชื่อจังหวัดหลุดมาไหม?
    target_folder_ids = []
    detected_provinces = []
    
    for prov_name, prov_id in province_map.items():
        # ถ้าชื่อจังหวัด (เช่น 'น่าน') ปรากฏอยู่ในคำถาม user
        if prov_name in user_query:
            target_folder_ids.append(prov_id)
            detected_provinces.append(prov_name)
    
    # แจ้งเตือน user นิดหน่อยว่าบอทเจอจังหวัดนะ (Debug)
    if detected_provinces:
        st.toast(f"📍 กำลังค้นข้อมูลในโฟลเดอร์: {', '.join(detected_provinces)}")
    
    # 3. กำหนดเป้าหมายการค้นหา
    files_found = []
    
    if target_folder_ids:
        # กรณี A: เจอชื่อจังหวัด -> ค้นแค่ในโฟลเดอร์จังหวัดนั้นๆ (แม่นยำ 100%)
        for fid in target_folder_ids:
            # หาไฟล์ Text ในโฟลเดอร์นั้น
            q = f"'{fid}' in parents and mimeType = 'text/plain' and trashed = false"
            res = service.files().list(q=q, pageSize=10, fields="files(id, name)").execute()
            files_found.extend(res.get('files', []))
    else:
        # กรณี B: ไม่เจอชื่อจังหวัด -> ค้นหาแบบกว้าง (Keyword Search) ใน Root
        # หรือจะให้ดีคือ ค้นหาไฟล์ที่มีชื่อตรงกับ Keyword ในโฟลเดอร์ลูกทั้งหมด (อาจจะช้าหน่อย)
        # เพื่อความเร็ว เอาแค่หาไฟล์ที่มีชื่อตรงกับ Query ก็พอ
        st.toast("🔎 ไม่ระบุจังหวัด กำลังค้นหาจากชื่อไฟล์...")
        
        # ค้นหาไฟล์ที่มีชื่อตรงกับคำถาม (name contains '...')
        # หมายเหตุ: Drive API ค้นภาษาไทยใน 'name contains' ไม่ค่อยเก่ง แตพอลองดูได้
        clean_query = user_query.replace("ราคา", "").replace("ข้อมูล", "").strip()
        if clean_query:
            q = f"name contains '{clean_query}' and mimeType = 'text/plain' and trashed = false"
            res = service.files().list(q=q, pageSize=10, fields="files(id, name)").execute()
            files_found.extend(res.get('files', []))

    return files_found

# ================= Chat Logic =================
def ask_gemini_stream(file_list, question, timer_placeholder=None):
    service = get_drive_service()
    
    if not file_list:
        yield "ไม่พบเอกสารที่ตรงกับคำค้นหาในโฟลเดอร์ครับ ลองระบุชื่อจังหวัดให้ชัดเจนอีกนิดนะครับ"
        return

    # Parallel Download
    downloaded_texts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_file = {
            executor.submit(_download_single_file, f['id'], service, f['name']): f 
            for f in file_list
        }
        for future in concurrent.futures.as_completed(future_to_file):
            downloaded_texts.append(future.result())

    full_context = "\n".join(downloaded_texts)
    
    prompt = f"""
    Context: ข้อมูลเกษตรรายจังหวัด
    Task: ตอบคำถามโดยอ้างอิงข้อมูลด้านล่างนี้
    
    ข้อมูลอ้างอิง:
    {full_context}
    
    คำถาม: {question}
    """
    
    model = genai.GenerativeModel(MODEL_NAME)
    try:
        response = model.generate_content(
            prompt, stream=True, generation_config=GENERATION_CONFIG
        )
        for chunk in response:
            if chunk.text: yield chunk.text
    except Exception as e:
        yield f"⚠️ Error: {str(e)}"

def reply_general_chat(query):
    model = genai.GenerativeModel(MODEL_NAME)
    response = model.generate_content(query)
    return response.text
