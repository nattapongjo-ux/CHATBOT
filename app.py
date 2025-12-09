import streamlit as st
import os
import time
import backend

# =================ตั้งค่าหน้าเว็บ=================
st.set_page_config(page_title="Agricultural Data AI", page_icon="🌾", layout="wide")

# =================โหลด Config=================
try:
    API_KEY = st.secrets["API_KEY"]
    # ⚠️ DATA_PATH ใน secrets ต้องเป็น "Folder ID" (รหัสยาวๆ) นะครับ ไม่ใช่ Path เครื่อง
    DATA_PATH = st.secrets["DATA_PATH"] 
    backend.setup_api(API_KEY)
except Exception as e:
    st.error(f"❌ ตั้งค่าไม่สำเร็จ: {e}")
    st.stop()

# =================UI=================
st.markdown('<div class="main-title">🌾 ระบบคลังข้อมูลเกษตร 17 จังหวัด</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">AI Powered | Drive API Edition ☁️</div>', unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("ถามข้อมูลได้เลย..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        start_time = time.time()
        timer_placeholder = st.empty() # เอาไว้แสดงสถานะโหลด
        
        # 1. ค้นหาไฟล์จาก Drive (ส่ง Folder ID ไป)
        target_files = backend.find_relevant_files(DATA_PATH, prompt)
        
        if target_files:
            count = len(target_files)
            names = ", ".join([f['name'] for f in target_files[:3]])
            st.success(f"✅ พบ {count} ไฟล์จาก Drive: {names}...")
            
            response_placeholder = st.empty()
            full_text = ""
            
            # 2. เริ่ม Process (Download -> Answer)
            stream = backend.ask_gemini_stream(target_files, prompt, timer_placeholder)
            
            try:
                for chunk in stream:
                    full_text += chunk
                    response_placeholder.markdown(full_text + "▌")
                    
                    elapsed = time.time() - start_time
                    timer_placeholder.markdown(f"**⏱️ เวลา: {elapsed:.1f} วินาที**")
                
                response_placeholder.markdown(full_text)
                st.session_state.messages.append({"role": "assistant", "content": full_text})
                
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            # ไม่เจอไฟล์
            with st.spinner("ไม่พบข้อมูลใน Drive..."):
                reply = backend.reply_general_chat(prompt)
            st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
