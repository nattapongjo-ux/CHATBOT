import google.generativeai as genai
import streamlit as st
import json
import io
import concurrent.futures
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
# Import Error เพื่อดักจับกรณีโมเดลมีปัญหา
from google.api_core.exceptions import ResourceExhausted, InternalServerError, NotFound, GoogleAPICallError

# ================= Config =================
# ✅ ใช้ Gemini 2.0 Flash (ตัวล่าสุดของจริง เร็วที่สุดตอนนี้)
# หมายเหตุ: ถ้าอนาคตมี 2.5 มาจริง ให้แก้ตรงนี้เป็น 'gemini-2.5-flash' ได้เลย
PRIMARY_MODEL = 'gemini-2.5-flash' 

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
            
        # แปลง String JSON ให้เป็น Dictionary
        creds_info = json.loads(st.secrets["google_json"])
        
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        return build('drive', 'v3', credentials=creds)
    except json.JSONDecodeError:
        st.error("❌ รูปแบบ JSON ใน Secrets ผิดพลาด (เช็คตัวอักษรพิเศษหรือการกด Enter)")
        return None
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
    
    # Logic: หาชื่อจังหวัดในคำถาม
    for prov_name, prov_id in province_map.items():
        if prov_name in user_query:
            target_folder_ids.append(prov_id)
    
    files_found = []
    if target_folder_ids:
        # เจอจังหวัด -> ค้นในโฟลเดอร์จังหวัดนั้น
        for fid in target_folder_ids:
            q = f"'{fid}' in parents and mimeType = 'text/plain' and trashed = false"
            res = service.files().list(q=q, pageSize=10, fields="files(id, name)").execute()
            files_found.extend(res.get('files', []))
    else:
        # ไม่เจอจังหวัด -> ค้นหาจากชื่อไฟล์ (Keyword Search)
        clean_query = user_query.replace("ราคา", "").replace("ข้อมูล", "").strip()
        if clean_query:
            q = f"name contains '{clean_query}' and mimeType = 'text/plain' and trashed = false"
            res = service.files().list(q=q, pageSize=10, fields="files(id, name)").execute()
            files_found.extend(res.get('files', []))

    return files_found

# ================= Helper: เรียก Gemini แบบปลอดภัย (Safe Mode) =================
def _generate_content_safe(prompt, stream=False):
    """
    ระบบอัจฉริยะ: ลองใช้ 2.0 Flash ก่อน ถ้าไม่ได้ จะสลับไป 1.5 Flash ให้อัตโนมัติ
    """
    try:
        # รอบที่ 1: ลองใช้รุ่น PRIMARY (Gemini 2.0 Flash)
        model = genai.GenerativeModel(PRIMARY_MODEL)
        return model.generate_content(prompt, stream=stream, generation_config=GENERATION_CONFIG)
    
    except (ResourceExhausted, InternalServerError, NotFound, ValueError, GoogleAPICallError) as e:
        # รอบที่ 2: ถ้า Error (หาโมเดลไม่เจอ / โควตาเต็ม) -> สลับไปใช้รุ่น BACKUP
        # print(f"Switching model due to error: {e}") # (Debug ใน Console)
        time.sleep(1) # พัก 1 วิ
        model = genai.GenerativeModel(BACKUP_MODEL)
        return model.generate_content(prompt, stream=stream, generation_config=GENERATION_CONFIG)

# ================= Chat Logic =================
def ask_gemini_stream(file_list, question, timer_placeholder=None):
    service = get_drive_service()
    if not file_list:
        yield "ไม่พบเอกสารที่ตรงกับคำค้นหาในโฟลเดอร์ครับ (ลองระบุชื่อจังหวัดให้ชัดเจน)"
        return

    # Parallel Download (โหลดไฟล์พร้อมกัน เร็วปรู๊ด)
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
        # เรียกใช้ฟังก์ชัน Safe Mode
        response = _generate_content_safe(prompt, stream=True)
        for chunk in response:
            if chunk.text: yield chunk.text
            
    except Exception as e:
        yield f"⚠️ ขออภัยครับ ระบบ AI ขัดข้องชั่วคราว: {str(e)}"

def reply_general_chat(query):
    try:
        response = _generate_content_safe(query, stream=False)
        return response.text
    except Exception as e:
        return f"⚠️ ระบบไม่สามารถตอบกลับได้ในขณะนี้: {e}"

