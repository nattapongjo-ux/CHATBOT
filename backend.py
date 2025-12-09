import os
import time
import datetime
import google.generativeai as genai
import concurrent.futures
import streamlit as st

# ตั้งค่าโมเดล: เปลี่ยนเป็น 'Lite' เพื่อความเร็วสูงสุด (Latency ต่ำสุด)
MODEL_NAME = 'gemini-2.5-flash-lite'

# ตั้งค่า Safety Settings
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# ตั้งค่า Generation Config เพื่อเร่งความเร็วการตอบ
GENERATION_CONFIG = {
    "temperature": 0.3, # เพิ่มนิดหน่อยให้ภาษาดูเป็นธรรมชาติขึ้น แต่ยังคงความเร็ว
    "top_p": 0.8,
    "top_k": 40,
    "max_output_tokens": 8192,
}

def setup_api(api_key):
    """ตั้งค่า API Key และทำความสะอาด Key"""
    clean_key = api_key.strip()
    os.environ["GOOGLE_API_KEY"] = clean_key
    genai.configure(api_key=clean_key)

def find_relevant_files(root_folder, user_query):
    """
    ระบบค้นหาไฟล์อัจฉริยะ (Smart Search Logic)
    """
    found_files_map = {} 
    query_normalized = user_query.lower()
    
    # คำศัพท์ Trigger
    trigger_words = [
        "ทุกจังหวัด", "ทั้งหมด", "ทุกที่", "all provinces", "15 จังหวัด", "ทั่วประเทศ", "ภาพรวม",
        "จังหวัดไหน", "จังหวัดใด", "ที่ไหน", "อันดับ", "มากที่สุด", "น้อยที่สุด", "เปรียบเทียบ", "top", "rank",
        "จังหวัดอะไร", "อะไรบ้าง", "ที่ไหนบ้าง", "กี่จังหวัด",
        "แต่ละ", "รายจังหวัด", "แยกจังหวัด", "สรุป", "จำนวน", "มูลค่า", "รายได้", "แนวโน้ม", "สถิติ", "เฉลี่ย"
    ]
    
    is_search_all_trigger = any(trigger in query_normalized for trigger in trigger_words)

    # สแกนหาชื่อจังหวัดในคำถาม
    try:
        mentioned_provinces = []
        with os.scandir(root_folder) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name.lower() in query_normalized:
                    mentioned_provinces.append(entry.name.lower())
    except Exception:
        mentioned_provinces = []

    # เริ่มวนลูปค้นหา
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
            
            pdf_files = [f for f in filenames if f.lower().endswith(".pdf")]
            
            for filename in pdf_files:
                file_name_no_ext = os.path.splitext(filename)[0].lower()
                file_keywords = file_name_no_ext.replace("_", " ").replace("-", " ").split()
                file_keywords.append(file_name_no_ext) 

                current_score = 0
                for kw in file_keywords:
                    if len(kw) < 2 or kw in trigger_words: continue 
                    if kw in query_normalized: current_score += 50 

                if current_score == 0:
                    generic = ["ข้อมูลพื้นฐาน", "ข้อมูลทั่วไป", "รายงาน", "สรุป", "report", "basic", "profile", "data", "สถิติ", "ประจำปี"]
                    if any(t in file_name_no_ext for t in generic) or len(pdf_files) == 1:
                        current_score = 5

                if current_score > 0 and current_score >= max_score:
                    max_score = current_score
                    best_file = os.path.join(dirpath, filename)
            
            if best_file:
                found_files_map[folder_name] = best_file

    return list(found_files_map.values())

# --- ส่วนสำคัญที่เพิ่มความเร็ว: CACHING ---
@st.cache_resource(show_spinner=False, ttl=3600) # เก็บ Cache ไว้นาน 1 ชั่วโมง
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
            time.sleep(1) # รอ 1 วินาที
            uf = genai.get_file(uf.name)
            retry_count += 1
            if retry_count > 60:
                break
                
        return uf if uf.state.name != "FAILED" else None
    except Exception as e:
        print(f"Error uploading {path}: {e}")
        return None

def ask_gemini_stream(file_paths, question):
    """
    อัปโหลดไฟล์ (Parallel Max Power) -> ตอบแบบ Streaming
    """
    uploaded_files = []
    total = len(file_paths)
    
    # 1. Parallel Upload (ใช้ 15 threads เพื่อความเร็วสูงสุด)
    progress_bar = st.progress(0, text=f"🚀 เร่งความเร็วสูงสุด! กำลังเตรียมไฟล์ {total} รายการ...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
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
            progress_bar.progress(done / total, text=f"โหลดเสร็จแล้ว {done}/{total} ไฟล์ (Cache Active ⚡)")
    
    progress_bar.empty()

    if not uploaded_files:
        yield "❌ ไม่สามารถอ่านไฟล์ได้เลยครับ (Upload Failed)"
        return

    # 2. Prompt (กระชับขึ้นเพื่อลดเวลาประมวลผล)
    model = genai.GenerativeModel(MODEL_NAME)
    payload = uploaded_files + [
        f"""
        Context: Data Analyst for 15 provinces agriculture/coop data.
        Task: Answer question based ONLY on attached files ({len(uploaded_files)} files).
        
        Strict Rules:
        1. NO external knowledge.
        2. Convert Thai numerals (๑-๙) to Arabic (1-9).
        3. Use tables/lists for comparisons.
        4. Cite province names.
        5. Say "ไม่พบข้อมูล" if missing.
        
        Question: {question}
        """
    ]

    # 3. Streaming Response (เพิ่ม config เพื่อความเร็ว)
    try:
        response = model.generate_content(
            payload, 
            stream=True, 
            safety_settings=SAFETY_SETTINGS,
            generation_config=GENERATION_CONFIG # <--- ใส่ Config เร่งความเร็วตรงนี้
        )
        for chunk in response:
            if chunk.text:
                yield chunk.text
    except Exception as e:
        yield f"⚠️ เกิดข้อผิดพลาดในการสร้างคำตอบ: {str(e)}"

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
        2. หากถามข้อมูลตัวเลข/สถิติ: ตอบว่า "ไม่พบไฟล์เอกสารในระบบ Drive กรุณาระบุชื่อจังหวัดหรือหัวข้อให้ชัดเจน"
           
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
