import streamlit as st
import requests
import xml.etree.ElementTree as ET
import time
import smtplib
from email.mime.text import MIMEText
import uuid
from urllib.parse import quote
import sqlite3
import hashlib

# ==========================================
# ⚙️ 1. ตั้งค่าระบบและกุญแจส่วนกลาง 
# ==========================================
st.set_page_config(page_title="Payap Research Gen-AI", page_icon="🛡️", layout="wide")

# ดึงกุญแจจาก Streamlit Secrets
SYSTEM_EMAIL = st.secrets.get("SYSTEM_EMAIL", "")
SYSTEM_EMAIL_PWD = st.secrets.get("SYSTEM_EMAIL_PASSWORD", "")
CENTRAL_HF_TOKEN = st.secrets.get("HF_TOKEN", "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
")
CENTRAL_GEMINI_KEY = st.secrets.get("GEMINI_FREE_KEY", "AQ.Ab8RN6KOpdqNuoF8N4NBhZTBYCbsQ8zqOBHUX9cb1dAyYhs9vQ")
PUBMED_API_KEY = st.secrets.get("PUBMED_API_KEY", "55ca775dbcce505de81e116837ccbff61709
")

# ฐาน URL สำหรับแนบลิงก์ยืนยันตัวตน (เปลี่ยนเป็น URL จริงเมื่อ Deploy)
BASE_URL = st.secrets.get("BASE_URL", "http://localhost:8501")

# ==========================================
# 🗄️ 2. ระบบฐานข้อมูลและเข้ารหัสผ่าน (Database & Hashing)
# ==========================================
# สร้างการเชื่อมต่อ SQLite (เก็บเป็นไฟล์ users.db ในเครื่องเซิร์ฟเวอร์)
conn = sqlite3.connect('users.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, email TEXT, password TEXT, verified INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS verify_tokens (token TEXT PRIMARY KEY, username TEXT)''')
conn.commit()

def hash_password(password):
    """เข้ารหัสผ่านก่อนลงฐานข้อมูลเพื่อความปลอดภัย"""
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# 🔐 3. ระบบยืนยันตัวตน (Authentication & Email)
# ==========================================
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'username' not in st.session_state: st.session_state['username'] = ""

def send_verification_email(receiver_email, username, token):
    verify_link = f"{BASE_URL}/?token={token}"
    msg = MIMEText(f"เรียนคุณ {username},\n\nกรุณาคลิกลิงก์ด้านล่างเพื่อยืนยันบัญชีนักวิจัย:\n{verify_link}")
    msg['Subject'] = 'ยืนยันบัญชี Payap Research Gen-AI Hub'
    msg['From'] = SYSTEM_EMAIL
    msg['To'] = receiver_email

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SYSTEM_EMAIL, SYSTEM_EMAIL_PWD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False

def check_verification_url():
    """ตรวจสอบ URL ว่ามีการกดลิงก์ยืนยันมาหรือไม่"""
    if "token" in st.query_params:
        token = st.query_params["token"]
        c.execute('SELECT username FROM verify_tokens WHERE token=?', (token,))
        result = c.fetchone()
        
        if result:
            user = result[0]
            # อัปเดตสถานะ verified = 1 (True)
            c.execute('UPDATE users SET verified=1 WHERE username=?', (user,))
            c.execute('DELETE FROM verify_tokens WHERE token=?', (token,))
            conn.commit()
            st.success(f"✅ บัญชี '{user}' ยืนยันสำเร็จ เข้าสู่ระบบได้เลยครับ")
        else:
            st.error("❌ ลิงก์ไม่ถูกต้องหรือถูกใช้งานไปแล้ว")
        st.query_params.clear()

def login_register_page():
    check_verification_url()
    st.title("🏛️ Payap Research Gen-AI Hub")
    tab1, tab2 = st.tabs(["เข้าสู่ระบบ", "สมัครสมาชิก"])
    
    with tab1:
        log_user = st.text_input("ชื่อผู้ใช้", key="log_user")
        log_pwd = st.text_input("รหัสผ่าน", type="password", key="log_pwd")
        if st.button("เข้าสู่ระบบ"):
            hashed_pwd = hash_password(log_pwd)
            c.execute('SELECT password, verified FROM users WHERE username=?', (log_user,))
            user_record = c.fetchone()
            
            if user_record and user_record[0] == hashed_pwd:
                if user_record[1] == 1:
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = log_user
                    st.rerun()
                else:
                    st.warning("⚠️ กรุณายืนยันลิงก์ในอีเมลก่อนครับ")
            else:
                st.error("❌ ข้อมูลไม่ถูกต้อง")

    with tab2:
        reg_user = st.text_input("ชื่อผู้ใช้ใหม่")
        reg_email = st.text_input("อีเมล")
        reg_pwd = st.text_input("รหัสผ่าน", type="password")
        if st.button("สมัครและรับอีเมลยืนยัน"):
            c.execute('SELECT username FROM users WHERE username=?', (reg_user,))
            if c.fetchone():
                st.warning("ชื่อผู้ใช้นี้มีในระบบแล้ว")
            elif reg_user and reg_pwd and "@" in reg_email:
                hashed_pwd = hash_password(reg_pwd)
                c.execute('INSERT INTO users (username, email, password, verified) VALUES (?, ?, ?, 0)', 
                          (reg_user, reg_email, hashed_pwd))
                token = str(uuid.uuid4())
                c.execute('INSERT INTO verify_tokens (token, username) VALUES (?, ?)', (token, reg_user))
                conn.commit()
                
                with st.spinner("กำลังส่งอีเมล..."):
                    if send_verification_email(reg_email, reg_user, token):
                        st.success("ส่งอีเมลสำเร็จ! กรุณากดลิงก์เพื่อเปิดใช้งาน")
                    else:
                        st.error("ไม่สามารถส่งอีเมลได้ ตรวจสอบการตั้งค่า SMTP")

# ==========================================
# 📚 4. ระบบดึงฐานข้อมูล (PubMed API)
# ==========================================
def search_pubmed_stable(query, max_results=3, retries=3):
    safe_query = quote(query)
    base_params = f"&tool=PayapGenAI&email=research@payap.ac.th"
    if PUBMED_API_KEY: base_params += f"&api_key={PUBMED_API_KEY}"
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={safe_query}&retmax={max_results}&retmode=json{base_params}"
    
    try:
        res = requests.get(url, timeout=10)
        ids = res.json().get('esearchresult', {}).get('idlist', [])
        if not ids: return "ไม่พบเปเปอร์ใน PubMed"
        time.sleep(0.2)
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={','.join(ids)}&retmode=xml{base_params}"
        fetch_res = requests.get(fetch_url, timeout=15)
        root = ET.fromstring(fetch_res.content)
        results = []
        for article in root.findall('.//PubmedArticle'):
            title = article.find('.//ArticleTitle')
            title_text = title.text if title is not None else "ไม่มีชื่อเรื่อง"
            abs_tag = article.find('.//AbstractText')
            abs_text = abs_tag.text[:500] + "..." if abs_tag is not None and abs_tag.text else "ไม่มีบทคัดย่อ"
            results.append(f"Title: {title_text}\nAbstract: {abs_text}")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error PubMed: {e}"

# ==========================================
# 🤖 5. ระบบ RAG & สมองกล AI (AI Inference)
# ==========================================
def ask_gemini(prompt, api_key):
    """เรียกใช้ Google Gemini API แบบตรงผ่าน HTTP Requests"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"Gemini Error: {response.text}"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"

def ask_huggingface(prompt, model_repo, api_key):
    """เรียกใช้โมเดล Open Source จาก Hugging Face"""
    url = f"https://api-inference.huggingface.co/models/{model_repo}"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 800, "temperature": 0.3}}
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            return response.json()[0].get('generated_text', 'ไม่สามารถสร้างข้อความได้')
        return f"HF Error: {response.text}"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"

# ==========================================
# 💻 6. หน้าต่างปฏิบัติการ (Main Workspace)
# ==========================================
def main_app():
    with st.sidebar:
        st.header(f"👨‍🔬 นักวิจัย: {st.session_state['username']}")
        if st.button("ออกจากระบบ"):
            st.session_state['logged_in'] = False
            st.rerun()
            
        st.divider()
        st.header("⚙️ ตั้งค่ามันสมอง AI")
        ai_mode = st.radio("โหมดการเข้าถึง:", ["🌟 ฟรี (ส่วนกลาง)", "🔑 ขั้นสูง (BYOK)"])
        
        selected_model, active_key = None, None
        
        if ai_mode == "🌟 ฟรี (ส่วนกลาง)":
            selected_model = st.selectbox("เลือก AI:", ["Gemini 1.5 Flash", "SeaLLMs/SeaLLM-7B-v2.5 (ภาษาไทย)"])
            active_key = CENTRAL_GEMINI_KEY if "Gemini" in selected_model else CENTRAL_HF_TOKEN
        else:
            selected_model = st.selectbox("เลือกรุ่น Pro (เสียค่าใช้จ่าย):", ["Google Gemini 1.5 Pro", "OpenAI GPT-4o"])
            active_key = st.text_input("🔑 ใส่ API Key:", type="password")

    st.title("🔬 ระบบประมวลผลงานวิจัยอัจฉริยะ")
    if "messages" not in st.session_state: st.session_state.messages = []
    
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if query := st.chat_input("พิมพ์คำสั่ง (เช่น 'หาเปเปอร์เกี่ยวกับมะเร็งใน pubmed แล้วสรุปให้หน่อย')"):
        if ai_mode == "🔑 ขั้นสูง (BYOK)" and not active_key:
            st.error("⚠️ โหมด BYOK บังคับให้ใส่ API Key ส่วนตัวก่อนครับ!")
            return

        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"): st.markdown(query)

        with st.chat_message("assistant"):
            with st.spinner(f"กำลังประมวลผลด้วย {selected_model}..."):
                response_ui = ""
                context_text = ""
                
                # ตรวจจับการสั่งค้นหา PubMed
                if "pubmed" in query.lower():
                    search_kw = query.lower().replace("ใน pubmed", "").replace("หาเปเปอร์", "").strip()
                    context_text = search_pubmed_stable(search_kw)
                    response_ui += f"**📖 ข้อมูลอ้างอิงจาก PubMed:**\n{context_text}\n\n---\n"
                
                # เตรียม Prompt สำหรับให้ AI อ่านและสรุป
                ai_prompt = query
                if context_text and "Error" not in context_text and "ไม่พบ" not in context_text:
                    ai_prompt = f"โปรดทำหน้าที่เป็นนักวิจัยผู้เชี่ยวชาญ สรุปข้อมูลและตอบคำถามต่อไปนี้โดยอิงจากบทคัดย่องานวิจัยที่ให้มาเป็นหลัก\n\n[ข้อมูลอ้างอิง]:\n{context_text}\n\n[คำถาม]: {query}"

                # ยิงคำสั่งเข้าแกนสมอง AI
                ai_result = ""
                if "Gemini" in selected_model:
                    ai_result = ask_gemini(ai_prompt, active_key)
                elif "SeaLLMs" in selected_model:
                    ai_result = ask_huggingface(ai_prompt, selected_model, active_key)
                else:
                    ai_result = "ระบบ BYOK สำหรับค่ายอื่นเตรียมเปิดใช้งานในระยะต่อไป (กรุณาใช้ Gemini ไปก่อนครับ)"

                response_ui += f"🤖 **[สรุปผลการวิเคราะห์]:**\n{ai_result}"
                
                st.markdown(response_ui)
                st.session_state.messages.append({"role": "assistant", "content": response_ui})

# ==========================================
# 🚀 7. ตัวจุดระเบิดระบบ
# ==========================================
if not st.session_state['logged_in']: login_register_page()
else: main_app()
