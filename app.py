import streamlit as st
import os
import time
import backend

# =================ตั้งค่าระบบ (Config)=================
try:
    API_KEY = st.secrets["API_KEY"]
    DATA_PATH = st.secrets["DATA_PATH"]
except FileNotFoundError:
    st.error("❌ ไม่พบไฟล์ secrets.toml กรุณาสร้างไฟล์ .streamlit/secrets.toml ก่อนเริ่มใช้งาน")
    st.stop()
except KeyError as e:
    st.error(f"❌ ไม่พบค่า {e} ในไฟล์ secrets.toml")
    st.stop()

st.set_page_config(page_title="Agricultural Data AI", page_icon="🌾", layout="wide")

try:
    backend.setup_api(API_KEY)
except Exception as e:
    st.error(f"ตั้งค่า API ไม่สำเร็จ: {e}")

# =================ฟังก์ชันใหม่: เช็คโฟลเดอร์จังหวัด=================
def get_files_from_province_folder(base_path, user_prompt):
    """
    เช็คว่าใน prompt มีชื่อจังหวัด(โฟลเดอร์) หรือไม่ 
    ถ้ามี -> คืนค่า list ไฟล์ทั้งหมดในโฟลเดอร์นั้น
    ถ้าไม่มี -> คืนค่า list ว่าง
    """
    if not os.path.exists(base_path):
        return [], None

    # 1. ดึงรายชื่อโฟลเดอร์ทั้งหมด (สมมติว่าเป็นชื่อจังหวัด)
    all_folders = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]
    
    matched_province = None
    target_files = []

    # 2. วนลูปเช็คว่ามีชื่อจังหวัดอยู่ในคำถามไหม
    for folder in all_folders:
        # เช็คแบบง่าย: ถ้าชื่อโฟลเดอร์อยู่ใน prompt (เช่น prompt="ข้าวจังหวัดตาก", folder="ตาก")
        if folder in user_prompt: 
            matched_province = folder
            break
    
    # 3. ถ้าเจอจังหวัด ให้ดึงไฟล์ทั้งหมดในนั้น
    if matched_province:
        folder_path = os.path.join(base_path, matched_province)
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                # กรองเฉพาะไฟล์เอกสารที่รองรับ
                if file.lower().endswith(('.csv', '.xlsx', '.xls', '.txt', '.pdf', '.docx', '.json')):
                    target_files.append(os.path.join(root, file))
                    
    return target_files, matched_province

# =================ส่วนแสดงผล (UI)=================
def load_css(file_name):
    with open(file_name, encoding="utf-8") as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

if os.path.exists("style.css"):
    load_css("style.css")

st.markdown('<div class="main-title">🌾 ระบบคลังข้อมูลเกษตร 17 จังหวัด</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">AI Powered Data Analysis | Gemini 2.5 Flash Lite</div>', unsafe_allow_html=True)

if not os.path.exists(DATA_PATH):
    st.error(f"❌ ไม่พบโฟลเดอร์: {DATA_PATH}")
    st.info("กรุณาแก้ไข DATA_PATH ในไฟล์ app.py ให้ถูกต้อง")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("ถามข้อมูลได้เลย... (เช่น 'ข้อมูลสหกรณ์จังหวัดตาก')"):
    
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    greetings = ["สวัสดี", "ดีครับ", "ดีค่ะ", "ทักทาย", "test", "เทส", "hi", "hello"]
    if any(g == prompt.lower() for g in greetings) or (any(g in prompt.lower() for g in greetings) and len(prompt) < 15):
        response_text = "สวัสดีครับ! 👋 ผมคือ AI ผู้ช่วยข้อมูลเกษตร มีข้อมูลสถิติและสหกรณ์ทั้ง 17 จังหวัด ถามผมได้เลยครับ!"
        with st.chat_message("assistant"):
            st.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
        st.stop()

    with st.chat_message("assistant"):
        start_time = time.time()
        
        # ================= LOGIC ใหม่เริ่มตรงนี้ =================
        # 1. เช็คก่อนว่าระบุจังหวัด(ชื่อโฟลเดอร์)มาหรือไม่
        target_files, province_name = get_files_from_province_folder(DATA_PATH, prompt)
        
        # 2. ถ้าไม่เจอชื่อจังหวัด ให้ใช้ logic เดิม (ค้นหาตาม Keyword หรือ Backend เดิม)
        # หรือถ้าคุณต้องการบังคับว่าต้องใส่จังหวัดเท่านั้น ให้แก้ else เป็นแจ้งเตือน
        if not target_files:
             target_files = backend.find_relevant_files(DATA_PATH, prompt)
             display_msg = f"🔍 ค้นหาจากเนื้อหาไฟล์ (ไม่ระบุจังหวัด)"
        else:
             display_msg = f"📂 พบข้อมูลจังหวัด: **{province_name}**"
        # ======================================================

        if target_files:
            count = len(target_files)
            st.success(f"✅ {display_msg} จำนวน {count} ไฟล์")
            
            timer_placeholder = st.empty()
            timer_placeholder.markdown("**⏱️ เวลาที่ใช้ไป: 0.0 วินาที**")
            
            response_placeholder = st.empty()
            full_text = ""
            
            with st.status("🚀 กำลังประมวลผล...", expanded=True) as status:
                st.write("📂 กำลังอ่านไฟล์ในโฟลเดอร์...")
                
                stream_generator = backend.ask_gemini_stream(
                    target_files, 
                    prompt, 
                    timer_placeholder=timer_placeholder, 
                    start_time=start_time
                )
                
                try:
                    first_chunk = next(stream_generator)
                    status.update(label="✅ วิเคราะห์เสร็จสิ้น!", state="complete", expanded=False)
                    
                    full_text += first_chunk
                    response_placeholder.markdown(full_text + "▌")
                    
                    elapsed = time.time() - start_time
                    timer_placeholder.markdown(f"**⏱️ เวลาที่ใช้ไป: {elapsed:.1f} วินาที** (กำลังพิมพ์...)")

                    for chunk in stream_generator:
                        full_text += chunk
                        response_placeholder.markdown(full_text + "▌")
                        elapsed = time.time() - start_time
                        timer_placeholder.markdown(f"**⏱️ เวลาที่ใช้ไป: {elapsed:.1f} วินาที** (กำลังพิมพ์...)")
                    
                    response_placeholder.markdown(full_text)
                    
                    total_time = time.time() - start_time
                    timer_placeholder.markdown(f"**⏱️ เสร็จสิ้น! ใช้เวลาทั้งหมด: {total_time:.2f} วินาที**")
                    
                    st.session_state.messages.append({"role": "assistant", "content": full_text})

                except StopIteration:
                    status.update(label="❌ AI ไม่ตอบกลับ", state="error")
                    st.error("เกิดข้อผิดพลาด: AI ไม่ส่งข้อมูลกลับมา")
                except Exception as e:
                    status.update(label="❌ เกิดข้อผิดพลาด", state="error")
                    st.error(f"Error: {e}")
        
        else:
            with st.spinner("ไม่พบข้อมูลจังหวัดหรือไฟล์ที่เกี่ยวข้อง..."):
                reply = backend.reply_general_chat(prompt)
            
            st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
