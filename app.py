import streamlit as st
import os
import time
import backend  # เรียกใช้ backend.py ตัวเทพของคุณ

# =================ตั้งค่าหน้าเว็บ=================
st.set_page_config(page_title="Agricultural Data AI", page_icon="🌾", layout="wide")

# =================โหลด Config=================
try:
    # อ่านค่าจาก Secrets
    API_KEY = st.secrets["API_KEY"]
    
    # ⚠️ สำคัญ: บน Streamlit Cloud ค่านี้ต้องเป็นชื่อโฟลเดอร์ในโปรเจกต์ (เช่น "data")
    # ห้ามใส่ path ยาวๆ ของเครื่องตัวเอง (เช่น G:\My Drive\...)
    DATA_PATH = st.secrets["DATA_PATH"] 

    # ตั้งค่า API
    backend.setup_api(API_KEY)
except Exception as e:
    st.error(f"❌ ตั้งค่าไม่สำเร็จ: {e}")
    st.info("กรุณาตรวจสอบไฟล์ .streamlit/secrets.toml")
    st.stop()

# =================ส่วนโหลด CSS (ถ้ามี)=================
if os.path.exists("style.css"):
    with open("style.css", encoding="utf-8") as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

# =================UI=================
st.markdown('<div class="main-title">🌾 ระบบคลังข้อมูลเกษตร 17 จังหวัด</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">AI Powered | Gemini 2.5 Flash Lite (High Speed)</div>', unsafe_allow_html=True)

# เช็คว่ามีโฟลเดอร์ข้อมูลจริงไหม (สำคัญสำหรับโค้ดแบบ os.walk)
if not os.path.exists(DATA_PATH):
    st.error(f"❌ ไม่พบโฟลเดอร์ข้อมูล: '{DATA_PATH}'")
    st.warning("คำแนะนำ: บน Streamlit Cloud ให้สร้างโฟลเดอร์ชื่อ data ไว้ใน GitHub แล้วแก้ Secrets เป็น DATA_PATH = 'data'")
    st.stop()

# ประวัติแชท
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# กล่องรับข้อความ
if prompt := st.chat_input("ถามข้อมูลได้เลย... (เช่น 'เปรียบเทียบพื้นที่ปลูกข้าวรายจังหวัด')"):
    
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # ค้นหาไฟล์และตอบคำถาม
    with st.chat_message("assistant"):
        start_time = time.time()
        
        # ค้นหาไฟล์ด้วย os.walk (แบบ Local/GitHub)
        target_files = backend.find_relevant_files(DATA_PATH, prompt)
        
        if target_files:
            count = len(target_files)
            # แสดงชื่อไฟล์แบบย่อๆ
            file_names = [os.path.basename(f) for f in target_files[:3]]
            display_text = f"✅ พบ {count} ไฟล์: {', '.join(file_names)}"
            if count > 3: display_text += f" และอีก {count-3} ไฟล์"
            st.success(display_text)
            
            timer_placeholder = st.empty()
            response_placeholder = st.empty()
            full_text = ""
            
            # Streaming Response
            stream_generator = backend.ask_gemini_stream(target_files, prompt)
            
            try:
                for chunk in stream_generator:
                    full_text += chunk
                    response_placeholder.markdown(full_text + "▌")
                    
                    elapsed = time.time() - start_time
                    timer_placeholder.markdown(f"**⏱️ เวลา: {elapsed:.1f} วินาที**")
                
                # จบการพิมพ์
                response_placeholder.markdown(full_text)
                st.session_state.messages.append({"role": "assistant", "content": full_text})
                
                total_time = time.time() - start_time
                timer_placeholder.markdown(f"**⏱️ เสร็จสิ้น! ใช้เวลา: {total_time:.2f} วินาที**")

            except Exception as e:
                st.error(f"Error: {e}")
        
        else:
            # ไม่เจอไฟล์ -> ตอบ Chat เล่นๆ
            with st.spinner("ไม่พบเอกสาร... กำลังคิดคำตอบทั่วไป..."):
                reply = backend.reply_general_chat(prompt)
            st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
