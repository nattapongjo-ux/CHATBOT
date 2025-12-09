import streamlit as st
import os
import time
import backend

# =================ตั้งค่าระบบ (Config)=================
# โหลดค่าจาก st.secrets แทนการพิมพ์ตรงๆ
try:
    API_KEY = st.secrets["API_KEY"]
    DATA_PATH = st.secrets["DATA_PATH"]
except FileNotFoundError:
    st.error("❌ ไม่พบไฟล์ secrets.toml กรุณาสร้างไฟล์ .streamlit/secrets.toml ก่อนเริ่มใช้งาน")
    st.stop()
except KeyError as e:
    st.error(f"❌ ไม่พบค่า {e} ในไฟล์ secrets.toml")
    st.stop()

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="Agricultural Data AI", page_icon="🌾", layout="wide")

# เชื่อมต่อ Backend
try:
    backend.setup_api(API_KEY)
except Exception as e:
    st.error(f"ตั้งค่า API ไม่สำเร็จ: {e}")

# =================ส่วนโหลด CSS=================
def load_css(file_name):
    with open(file_name, encoding="utf-8") as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

if os.path.exists("style.css"):
    load_css("style.css")

# =================ส่วนแสดงผล (UI)=================
st.markdown('<div class="main-title">🌾 ระบบคลังข้อมูลเกษตร 17 จังหวัด</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">AI Powered Data Analysis | Gemini 2.5 Flash Lite</div>', unsafe_allow_html=True)

# เช็ค Path ข้อมูล
if not os.path.exists(DATA_PATH):
    st.error(f"❌ ไม่พบโฟลเดอร์: {DATA_PATH}")
    st.info("กรุณาแก้ไข DATA_PATH ในไฟล์ app.py ให้ถูกต้อง")
    st.stop()

# ประวัติแชท
if "messages" not in st.session_state:
    st.session_state.messages = []

# แสดงประวัติเก่า
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# กล่องรับข้อความ
if prompt := st.chat_input("ถามข้อมูลได้เลย... (เช่น 'เปรียบเทียบพื้นที่ปลูกข้าวรายจังหวัด')"):
    
    # 1. แสดงคำถามผู้ใช้
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. เริ่มกระบวนการคิด (เช็คคำทักทาย)
    greetings = ["สวัสดี", "ดีครับ", "ดีค่ะ", "ทักทาย", "test", "เทส", "hi", "hello"]
    
    if any(g == prompt.lower() for g in greetings) or (any(g in prompt.lower() for g in greetings) and len(prompt) < 15):
        response_text = "สวัสดีครับ! 👋 ผมคือ AI ผู้ช่วยข้อมูลเกษตร มีข้อมูลสถิติและสหกรณ์ทั้ง 17 จังหวัด ถามผมได้เลยครับ!"
        with st.chat_message("assistant"):
            st.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
        st.stop()

    # 3. ค้นหาไฟล์และตอบคำถาม
    with st.chat_message("assistant"):
        # เริ่มจับเวลา
        start_time = time.time()
        
        target_files = backend.find_relevant_files(DATA_PATH, prompt)
        
        if target_files:
            # เจอไฟล์ -> แสดงจำนวนไฟล์
            count = len(target_files)
            if count > 5:
                st.success(f"✅ พบข้อมูล **{count} จังหวัด**")
            else:
                names = ", ".join([os.path.basename(f) for f in target_files])
                st.success(f"✅ พบข้อมูล {count} ไฟล์: **{names}**")
            
            # --- สร้างพื้นที่แสดงเวลา (Timer) ---
            # สร้างไว้ตรงนี้เพื่อส่งไปให้ backend อัปเดต
            timer_placeholder = st.empty()
            timer_placeholder.markdown("**⏱️ เวลาที่ใช้ไป: 0.0 วินาที**")
            
            response_placeholder = st.empty()
            full_text = ""
            
            # --- ส่วนแสดงสถานะ (Status) ---
            with st.status("🚀 กำลังประมวลผล...", expanded=True) as status:
                st.write("📂 กำลังเตรียมข้อมูล...")
                
                # เรียก API และส่ง timer_placeholder ไปด้วย!
                # Backend จะช่วยอัปเดตเวลาระหว่างโหลดไฟล์ให้
                stream_generator = backend.ask_gemini_stream(
                    target_files, 
                    prompt, 
                    timer_placeholder=timer_placeholder, 
                    start_time=start_time
                )
                
                try:
                    # รอคำตอบคำแรก (Blocking Wait)
                    # ช่วงนี้เวลาอาจจะนิ่งไปสักพักจนกว่า AI จะเริ่มตอบ
                    first_chunk = next(stream_generator)
                    
                    # พอได้คำแรก -> อัปเดตสถานะว่าเสร็จ
                    status.update(label="✅ วิเคราะห์เสร็จสิ้น!", state="complete", expanded=False)
                    
                    # พิมพ์คำแรก
                    full_text += first_chunk
                    response_placeholder.markdown(full_text + "▌")
                    
                    # อัปเดตเวลาทันที
                    elapsed = time.time() - start_time
                    timer_placeholder.markdown(f"**⏱️ เวลาที่ใช้ไป: {elapsed:.1f} วินาที** (กำลังพิมพ์...)")

                    # วนลูปพิมพ์คำที่เหลือ
                    for chunk in stream_generator:
                        full_text += chunk
                        response_placeholder.markdown(full_text + "▌")
                        
                        # อัปเดตเวลาตลอดการพิมพ์
                        elapsed = time.time() - start_time
                        timer_placeholder.markdown(f"**⏱️ เวลาที่ใช้ไป: {elapsed:.1f} วินาที** (กำลังพิมพ์...)")
                    
                    # เสร็จสิ้น
                    response_placeholder.markdown(full_text)
                    
                    # เวลาสุดท้าย
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
            with st.spinner("ไม่พบเอกสารตรงๆ... กำลังตรวจสอบคำถาม..."):
                reply = backend.reply_general_chat(prompt)
            
            st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})