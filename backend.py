import google.generativeai as genai
import streamlit as st
import json
import io
import concurrent.futures
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.api_core.exceptions import ResourceExhausted, InternalServerError # เพิ่มตัวนี้เข้ามา

# ================= Config =================
# โมเดลหลัก (เร็วแรง)
PRIMARY_MODEL = 'gemini-2.0-flash-lite-preview-02-05' 
# โมเดลสำรอง (เสถียร) - ใช้เมื่อตัวหลักโควตาเต็ม
BACKUP_MODEL = 'gemini-1.5-flash'

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

# ================= Province Map Logic =================
@st.cache_data(ttl=3600)
def get_province_map(root_folder_id):
    service = get_drive_service()
    if not service: return {}
    try:
        query = f"'{root_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(q=query, pageSize=100, fields="files(id, name)").execute()
        return {f['name'].strip(): f['id'] for f in results.get('files', [])}
    except Exception as e:
        print(f"Error mapping provinces: {e}")
        return {}

def find_relevant_files(root_folder_id, user_query):
    service = get_drive_service()
    if not service: return []
    
    province_map = get_province_map(root_folder_id)
    target_folder_ids = []
    detected_provinces = []
    
    for prov_name, prov_id in province_map.items():
        if prov_name in user_query:
            target_folder_ids.append(prov_id)
            detected_provinces.append(prov_name)
    
    files_found = []
    if target_folder_ids:
        # st.toast(f"📍 เจอจังหวัด: {', '.join(detected_provinces)}")
        for fid in target_folder_ids:
            q = f"'{fid}' in parents and mimeType = 'text/plain' and trashed = false"
            res = service.files().list(q=q, pageSize=10, fields="files(id, name)").execute()
            files_found.extend(res.get('files', []))
    else:
        # st.toast("🔎 ค้นหาจากชื่อไฟล์...")
        clean_query = user_query.replace("ราคา", "").replace("ข้อมูล", "").strip()
        if clean_query:
            q = f"name contains '{clean_query}' and mimeType = 'text/plain' and trashed = false"
            res = service.files().list(q=q, pageSize=10, fields="files(id, name)").execute()
            files_found.extend(res.get('files', []))

    return files_found

# ================= Helper: เรียก Gemini แบบปลอดภัย =================
def _generate_content_safe(prompt, stream=False):
    """
    ฟังก์ชันนี้จะลองใช้รุ่น Lite ก่อน ถ้าโควตาเต็ม จะสลับไปใช้รุ่นปกติให้อัตโนมัติ
    """
    try:
        # รอบที่ 1: ลองใช้รุ่น Lite (เร็วสุด)
        model = genai.GenerativeModel(PRIMARY_MODEL)
        return model.generate_content(prompt, stream=stream, generation_config=GENERATION_CONFIG)
    
    except (ResourceExhausted, InternalServerError):
        # รอบที่ 2: ถ้า Error (โควตาเต็ม/ล่ม) -> สลับไปใช้รุ่น Backup
        # st.toast("⚠️ รุ่น Lite โควตาเต็ม กำลังสลับไปใช้รุ่นมาตรฐาน...", icon="🔄")
        time.sleep(1) # พักหายใจ 1 วิ
        model = genai.GenerativeModel(BACKUP_MODEL)
        return model.generate_content(prompt, stream=stream, generation_config=GENERATION_CONFIG)

# ================= Chat Logic =================
def ask_gemini_stream(file_list, question, timer_placeholder=None):
    service = get_drive_service()
    if not file_list:
        yield "ไม่พบเอกสารที่ตรงกับคำค้นหาในโฟลเดอร์ครับ"
        return

    downloaded_texts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_file = {
            executor.submit(_download_single_file, f['id'], service, f['name']): f 
            for f in file_list
        }
        for future in concurrent.futures.as_completed(future_to_file):
            downloaded_texts.append(future.result())

    full_context = "\n".join(downloaded_texts)
    prompt = f"Context:\n{full_context}\n\nQuestion: {question}\nAnswer based on context:"
    
    try:
        # เรียกใช้ฟังก์ชัน Safe Mode ที่เราเขียนเพิ่ม
        response = _generate_content_safe(prompt, stream=True)
        
        for chunk in response:
            if chunk.text: yield chunk.text
            
    except Exception as e:
        yield f"⚠️ ขออภัยครับ ระบบ AI ขัดข้องชั่วคราว (Quota Exceeded): {str(e)}"

def reply_general_chat(query):
    try:
        # เรียกใช้ฟังก์ชัน Safe Mode แบบไม่ Stream
        response = _generate_content_safe(query, stream=False)
        return response.text
    except Exception as e:
        return f"⚠️ ระบบไม่สามารถตอบกลับได้ในขณะนี้: {e}"
