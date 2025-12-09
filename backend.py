import os
import time
import datetime
import google.generativeai as genai
import concurrent.futures
import streamlit as st

# ตั้งค่าโมเดล: ใช้ Flash Lite เพื่อความเร็ว
MODEL_NAME = 'gemini-2.5-flash-lite'

# ตั้งค่า Safety Settings
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# ตั้งค่า Generation Config
GENERATION_CONFIG = {
    "temperature": 0.3,
    "top_p": 0.8,
    "top_k": 40,
    "max_output_tokens": 8192,
}

def setup_api(api_key):
    """ตั้งค่า API Key"""
    clean_key = api_key.strip()
    os.environ["GOOGLE_API_KEY"] = clean_key
    genai.configure(api_key=clean_key)

# ================= Smart Search (Logic เดิมเอาไว้ Fallback) =================
def find_relevant_files(root_folder, user_query):
    """
    ระบบค้นหาไฟล์สำรอง (กรณีไม่เจอจังหวัด หรือค้นหาภาพรวม)
    """
    found_files_map = {} 
    query_normalized = user_query.lower()
    
    trigger_words = [
        "ทุกจังหวัด", "ทั้งหมด", "ทุกที่", "all provinces", "17 จังหวัด", "ทั่วประเทศ", "ภาพรวม",
        "จังหวัดไหน", "จังหวัดใด", "ที่ไหน", "อันดับ", "มากที่สุด", "น้อยที่สุด", "เปรียบเทียบ", "top", "rank",
        "จังหวัดอะไร", "อะไรบ้าง", "ที่ไหนบ้าง", "กี่จังหวัด",
        "แต่ละ", "รายจังหวัด", "แยกจังหวัด", "สรุป", "จำนวน", "มูลค่า", "รายได้", "แนวโน้ม", "สถิติ", "เฉลี่ย"
    ]
    
    is_search_all_trigger = any(trigger in query_normalized for trigger in trigger_words)

    # สแกนหาชื่อจังหวัดในคำถามเพื่อช่วยกรอง
    try:
        mentioned_provinces = []
        with os.scandir(root_folder) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name.lower() in query_normalized:
                    mentioned_provinces.append(entry.name.lower())
    except Exception:
        mentioned_provinces = []

    for dirpath, dirnames, filenames in os.walk(root_folder):
        folder_name = os.path.basename(dirpath).lower()
        should_check_folder = False

        if mentioned_provinces:
            if folder_name in mentioned_provinces: should_check_folder = True
        else:
            folder_match = (len(folder_name) > 1 and folder_name in query_normalized)
            should_check_folder = is_search_all_trigger or folder_match

        if should_check_folder:
            best_file = None
            max_score = 0
            
            # เน้นหา PDF ก่อน หรือไฟล์ Excel/CSV
            target_exts = [".pdf", ".xlsx", ".csv", ".txt"]
            candidate_files = [f for f in filenames if any(f.lower().endswith(ext) for ext in target_exts)]
            
            for filename in candidate_files:
                file_name_no_ext = os.path.splitext(filename)[0].lower()
                # Logic การให้คะแนนไฟล์อย่างง่าย
                current_score = 0
                if filename in query_normalized: current_score += 100
                if "สรุป" in filename or "report" in filename: current_score += 10
                
                # ถ้าเจาะจงจังหวัด ให้คะแนนไฟล์ในจังหวัดนั้นสูง
                if folder_name in query_normalized: current_score += 50

                if current_score >= max_score:
                    max_score = current_score
                    best_file = os.path.join(dirpath, filename)
            
            if best_file:
                found_files_map[folder_name] = best_file

    return list(found_files_map.values())

# ================= Caching Upload =================
@st.cache_resource(show_spinner=False, ttl=3600)
def _upload_single_cached(path, last_modified_time):
    """
    ฟังก์ชันอัปโหลดที่มีการจำค่า (Cache)
    """
    try:
        name = os.path.basename(path)
        uf = genai.upload_file(path=path, display_name=name)
        
        # รอ Processing
        retry_count = 0
        while uf.state.name == "PROCESSING":
            time.sleep(1)
            uf = genai.get_file(uf.name)
            retry_count += 1
            if retry_count > 60:
                break
                
        return uf if uf.state.name != "FAILED" else None
    except Exception as e:
        print(f"Error uploading {path}: {e}")
        return None

# ================= Main Gemini Function =================
def ask_gemini_stream(file_paths, question, timer_placeholder=None, start_time=None):
    """
    อัปโหลดไฟล์ -> ตอบแบบ Streaming
    รองรับ timer_placeholder เพื่ออัปเดตเวลาหน้าเว็บ
    """
    uploaded_files = []
    total = len(file_paths)
    
    # 1. Parallel Upload
    # แสดง Progress bar เฉพาะตอนอัปโหลด
    progress_text = f"🚀 กำลังเตรียมข้อมูล {total} ไฟล์..."
    progress_bar = st.progress(0, text=progress_text)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {}
        for path in file_paths:
            try:
                mtime = os.path.getmtime(path)
                future = executor.submit(_upload_single_cached, path, mtime)
                future_map[future] = path
            except:
                pass

        done = 0
        for future in concurrent.futures.as_completed(future_map):
            res = future.result()
            if res: uploaded_files.append(res)
            done += 1
            
            # อัปเดต Progress Bar
            progress_bar.progress(done / total, text=f"อ่านข้อมูลแล้ว {done}/{total} ไฟล์ (Cache Active ⚡)")
            
            # อัปเดตตัวจับเวลา (ถ้าส่งมา)
            if timer_placeholder and start_time:
                elapsed = time.time() - start_time
                timer_placeholder.markdown(f"**⏱️ เวลาที่ใช้ไป: {elapsed:.1f} วินาที** (กำลังอ่านไฟล์...)")
    
    progress_bar.empty() # ลบ Progress bar ออกเมื่อเสร็จ

    if not uploaded_files:
        yield "❌ ไม่สามารถอ่านไฟล์ได้เลยครับ (Upload Failed)"
        return

    # 2. Prompt Construction
    model = genai.GenerativeModel(MODEL_NAME)
    payload = uploaded_files + [
        f"""
        Role: Agricultural Data Specialist for Thailand (17 Provinces).
        Task: Analyze the provided documents to answer the question accurately.
        
        Question: "{question}"
        
        Guidelines:
        - Answer based ONLY on the provided files.
        - If the user asks about a specific province (e.g., Tak), focus heavily on the files from that folder.
        - Use Thai language.
        - Convert Thai numerals (๑-๙) to Arabic (1-9).
        - If comparing data, use a Table or Bullet points.
        - If data is missing, state clearly "ไม่พบข้อมูลในเอกสาร".
        """
    ]

    # 3. Streaming Response
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
        yield f"⚠️ เกิดข้อผิดพลาดในการสร้างคำตอบ: {str(e)}"

# ================= General Chat =================
def reply_general_chat(user_query):
    """
    ฟังก์ชันคุยเล่น (เมื่อหาไฟล์ไม่เจอ)
    """
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        now = datetime.datetime.now().strftime("%H:%M น.")
        
        prompt = f"""
        บทบาท: AI ผู้ช่วยคลังข้อมูลเกษตร (สุภาพ, เป็นกันเอง)
        
        คำสั่ง:
        1. หากเป็นการทักทาย/ถามเวลา: ตอบกลับสุภาพ (เวลา: {now})
        2. หากถามข้อมูลตัวเลข/สถิติ: ตอบว่า "ไม่พบไฟล์เอกสารในระบบ Drive หรือไม่ได้ระบุจังหวัด กรุณาระบุชื่อจังหวัดให้ชัดเจนครับ"
            
        User: {user_query}
        """
        
        response = model.generate_content(
            prompt, 
            safety_settings=SAFETY_SETTINGS,
            generation_config=GENERATION_CONFIG
        )
        return response.text
            
    except Exception as e:
        return f"ขออภัยครับ ระบบ AI ขัดข้องชั่วคราว: {str(e)}"
