import streamlit as st
import os
import time
import backend

# อ่านค่า Config
try:
    API_KEY = st.secrets["API_KEY"]
    DRIVE_FOLDER_ID = st.secrets["DRIVE_FOLDER_ID"] # ค่า ID ยาวๆ ของโฟลเดอร์หลัก
except Exception:
    st.error("❌ กรุณาตั้งค่า API_KEY และ DRIVE_FOLDER_ID ใน secrets.toml")
    st.stop()

st.set_page_config(page_title="Agri Data Live Drive", page_icon="☁️", layout="wide")

try:
    backend.setup_api(API_KEY)
except Exception as e:
    st.error(f"API Error: {e}")

st.title("🌾 ระบบคลังข้อมูลเกษตร (เชื่อมต่อ Google Drive Real-time)")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("ถามข้อมูลได้เลย... (เช่น 'ข้อมูลเกษตรจังหวัดตาก')"):
    
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # เช็คคำทักทาย
    if len(prompt) < 10 and any(x in prompt for x in ["ดี", "สวัสดี", "hi"]):
        reply = "สวัสดีครับ! ผมดึงข้อมูลสดจาก Google Drive ถามมาได้เลยครับ"
        st.chat_message("assistant").markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.stop()

    with st.chat_message("assistant"):
        start_time = time.time()
        
        # ======================================================
        # 🔥 เรียกใช้ฟังก์ชันดึงข้อมูลจาก Drive (ส่วนสำคัญ)
        # ======================================================
        with st.spinner("☁️ กำลังค้นหาและดาวน์โหลดข้อมูลจาก Google Drive..."):
            # ฟังก์ชันนี้จะไปดูใน Drive -> ถ้าเจอชื่อจังหวัด -> โหลดไฟล์ -> คืนค่า Path ไฟล์ในเครื่อง Server
            target_files, province_name = backend.check_drive_folder_and_download(DRIVE_FOLDER_ID, prompt)
        
        if target_files:
            st.success(f"✅ พบโฟลเดอร์: **{province_name}** (ดาวน์โหลดมา {len(target_files)} ไฟล์)")
            
            response_placeholder = st.empty()
            full_text = ""
            
            # ส่งไฟล์ที่โหลดมาแล้ว (อยู่ในเครื่อง Server) ไปให้ Gemini
            stream = backend.ask_gemini_stream(target_files, prompt)
            
            for chunk in stream:
                full_text += chunk
                response_placeholder.markdown(full_text + "▌")
            
            response_placeholder.markdown(full_text)
            st.session_state.messages.append({"role": "assistant", "content": full_text})
            
        else:
            reply = "🔍 ไม่พบชื่อจังหวัดในคำถาม หรือไม่พบโฟลเดอร์ใน Drive ครับ (กรุณาระบุชื่อจังหวัดให้ชัดเจน)"
            st.warning(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
