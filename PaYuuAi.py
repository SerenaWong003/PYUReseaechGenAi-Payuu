import streamlit as st
import requests
import xml.etree.ElementTree as ET
import time
import re
import os
import hashlib
import binascii
import sqlite3
import uuid
from datetime import datetime
from urllib.parse import quote
import google.generativeai as genai
import pandas as pd # เพิ่ม pandas สำหรับทำตารางแผงควบคุม Admin

# ==========================================
# ⚙️ 1. ตั้งค่าระบบและกุญแจส่วนกลาง
# ==========================================
st.set_page_config(page_title="Payap Research Gen-AI : Payuu", page_icon="🌪️", layout="wide")

CENTRAL_HF_TOKEN = st.secrets.get("HF_TOKEN","")
CENTRAL_GEMINI_KEY = st.secrets.get("GEMINI_FREE_KEY","")
PUBMED_API_KEY = st.secrets.get("PUBMED_API_KEY","")

EBSCO_USER_ID = st.secrets.get("EBSCO_USER_ID", "")
EBSCO_PASSWORD = st.secrets.get("EBSCO_PASSWORD", "")
EBSCO_PROFILE = st.secrets.get("EBSCO_PROFILE", "")
EBSCO_ORG = st.secrets.get("EBSCO_ORG", "")

_missing_core = [k for k, v in {
    "GEMINI_FREE_KEY": CENTRAL_GEMINI_KEY,
    "HF_TOKEN": CENTRAL_HF_TOKEN,
}.items() if not v]
if _missing_core:
    st.warning(f"⚠️ ยังไม่ได้ตั้งค่า secrets: {', '.join(_missing_core)} — โหมด 'ฟรี (ส่วนกลาง)' อาจใช้งานไม่ได้")

HF_MODELS = {
    "SeaLLMs (ภาษาไทย/Thai)": "SeaLLMs/SeaLLM-7B-v2.5",
    "Vicuna (ตรรกะ/Logic)": "lmsys/vicuna-7b-v1.5",
    "Alpaca (จัดการฟอร์แมต/Format)": "chavinlo/alpaca-native",
    "Gorilla (เขียนโค้ด/API)": "gorilla-llm/gorilla-7b-hf-v0",
    "ChatGLM (อ่านบริบทยาว/Long context)": "THUDM/chatglm3-6b",}
GEMINI_MODEL_MAP = {
    "Gemini 3.1 Flash (ล่าสุด)": "gemini-3.1-flash-preview",
    "Google Gemini 3.1 Pro (ล่าสุด)": "gemini-3.1-pro-preview",
    "Gemini (โมเดลพื้นฐาน)": "gemini-pro",
}
# ==========================================
# 🌐 ระบบภาษา (Language Translation Helper)
# ==========================================
if 'lang' not in st.session_state: st.session_state['lang'] = 'TH'

def t(th_text, en_text):
    return en_text if st.session_state['lang'] == 'EN' else th_text

# ==========================================
# 🗄️ 2. ระบบฐานข้อมูล v3
# ==========================================
conn = sqlite3.connect('payap_genai_v3.db', check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS users
             (username TEXT PRIMARY KEY, email TEXT, password TEXT, salt TEXT, verified INTEGER, role TEXT DEFAULT 'user')''')

c.execute('''CREATE TABLE IF NOT EXISTS chat_sessions
             (session_id TEXT PRIMARY KEY, username TEXT, title TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

c.execute('''CREATE TABLE IF NOT EXISTS chat_history
             (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, username TEXT, sender TEXT, message TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

PBKDF2_ITERATIONS = 200_000

def hash_password(password: str, salt_hex: str | None = None):
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return binascii.hexlify(pwd_hash).decode(), binascii.hexlify(salt).decode()

def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    if not stored_salt: return False
    check_hash, _ = hash_password(password, stored_salt)
    return check_hash == stored_hash

# ==========================================
# 🔐 3. ระบบยืนยันตัวตน
# ==========================================
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'username' not in st.session_state: st.session_state['username'] = ""
if 'role' not in st.session_state: st.session_state['role'] = "user"
if 'current_session_id' not in st.session_state: st.session_state['current_session_id'] = None
if 'messages_loaded' not in st.session_state: st.session_state['messages_loaded'] = False

def login_register_page():
    # ส่วนตั้งค่าภาษาก่อนล็อกอิน
    lang_sel = st.radio("Language / ภาษา", ["TH", "EN"], horizontal=True, key="login_lang")
    st.session_state['lang'] = lang_sel

    st.title(t("🏛️ Payap Research Gen-AI Hub", "🏛️ Payap Research Gen-AI Hub"))
    tab1, tab2 = st.tabs([t("เข้าสู่ระบบ", "Login"), t("สมัครสมาชิก", "Register")])

    with tab1:
        log_user = st.text_input(t("ชื่อผู้ใช้", "Username"), key="log_user")
        log_pwd = st.text_input(t("รหัสผ่าน", "Password"), type="password", key="log_pwd")
        if st.button(t("เข้าสู่ระบบ", "Login")):
            c.execute('SELECT password, salt, role FROM users WHERE username=?', (log_user,))
            user_record = c.fetchone()
            if user_record and verify_password(log_pwd, user_record[0], user_record[1]):
                st.session_state['logged_in'] = True
                st.session_state['username'] = log_user
                st.session_state['role'] = user_record[2]
                st.session_state['current_session_id'] = None
                st.session_state['messages_loaded'] = False
                st.rerun()
            else:
                st.error(t("❌ ข้อมูลไม่ถูกต้อง", "❌ Invalid credentials"))

    with tab2:
        reg_user = st.text_input(t("ชื่อผู้ใช้ใหม่", "New Username"))
        reg_email = st.text_input(t("อีเมล", "Email"))
        reg_pwd = st.text_input(t("รหัสผ่าน", "Password"), type="password", key="reg_pwd_input")
        if st.button(t("สมัครสมาชิก (เข้าระบบทันที)", "Register (Auto-login)")):
            c.execute('SELECT username FROM users WHERE username=?', (reg_user,))
            if c.fetchone():
                st.warning(t("⚠️ ชื่อผู้ใช้นี้มีในระบบแล้ว กรุณาใช้ชื่ออื่นครับ", "⚠️ Username already exists."))
            elif reg_user and reg_pwd:
                hashed_pwd, salt_hex = hash_password(reg_pwd)
                c.execute(
                    'INSERT INTO users (username, email, password, salt, verified, role) VALUES (?, ?, ?, ?, ?, ?)',
                    (reg_user, reg_email, hashed_pwd, salt_hex, 1, 'user')
                )
                conn.commit()
                st.success(t("✅ สมัครสมาชิกสำเร็จ! สลับไปแท็บ 'เข้าสู่ระบบ' เพื่อล็อกอินได้เลยครับ", "✅ Registration successful! Please login."))
            else:
                st.error(t("❌ กรุณากรอกข้อมูลให้ครบถ้วน", "❌ Please fill in all fields."))

_admin_user = st.secrets.get("ADMIN_USERNAME", "")
_admin_pass = st.secrets.get("ADMIN_PASSWORD", "")
if _admin_user and _admin_pass:
    c.execute('SELECT username FROM users WHERE username=?', (_admin_user,))
    if not c.fetchone():
        _h, _s = hash_password(_admin_pass)
        c.execute(
            'INSERT INTO users (username, email, password, salt, verified, role) VALUES (?, ?, ?, ?, ?, ?)',
            (_admin_user, f"{_admin_user}@payap.ac.th", _h, _s, 1, 'admin')
        )
        conn.commit()

# ==========================================
# 📚 4. ระบบดึงฐานข้อมูล (PubMed & EBSCO)
# ==========================================
# (คงฟังก์ชัน search_pubmed_stable, search_ebsco_stable, detect_sources, run_source_search ไว้เหมือนเดิมทุกประการ)
def search_pubmed_stable(query, max_results=3):
    safe_query = quote(query)
    base_params = "&tool=PayapGenAI&email=research@payap.ac.th"
    if PUBMED_API_KEY: base_params += f"&api_key={PUBMED_API_KEY}"
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={safe_query}&retmax={max_results}&retmode=json{base_params}"
    try:
        res = requests.get(url, timeout=10)
        ids = res.json().get('esearchresult', {}).get('idlist', [])
        if not ids: return t("ไม่พบเปเปอร์ใน PubMed", "No papers found in PubMed")
        time.sleep(0.2)
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={','.join(ids)}&retmode=xml{base_params}"
        fetch_res = requests.get(fetch_url, timeout=15)
        root = ET.fromstring(fetch_res.content)
        results = []
        for article in root.findall('.//PubmedArticle'):
            title = article.find('.//ArticleTitle')
            title_text = title.text if title is not None else "ไม่มีชื่อเรื่อง / No title"
            abs_tag = article.find('.//AbstractText')
            abs_text = abs_tag.text[:1000] + "..." if abs_tag is not None and abs_tag.text else "ไม่มีบทคัดย่อ / No abstract"
            results.append(f"[PubMed] Title: {title_text}\nAbstract: {abs_text}")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error PubMed: {e}"

def search_ebsco_stable(query, max_results=3):
    if not (EBSCO_USER_ID and EBSCO_PASSWORD and EBSCO_PROFILE):
        return t("⚠️ ยังไม่ได้ตั้งค่า EBSCO Secrets", "⚠️ EBSCO Secrets not configured")
    
    EBSCO_AUTH_URL = "https://eds-api.ebscohost.com/authservice/rest/UIDAuth"
    EBSCO_SESSION_URL = "https://eds-api.ebscohost.com/edsapi/rest/CreateSession"
    EBSCO_SEARCH_URL = "https://eds-api.ebscohost.com/edsapi/rest/Search"
    
    def _strip_html(text: str) -> str: return re.sub(r"<[^<]+?>", "", text or "")
    
    try:
        auth_res = requests.post(EBSCO_AUTH_URL, json={"UserId": EBSCO_USER_ID, "Password": EBSCO_PASSWORD, "Interface": "PayapGenAI"}, headers={"Content-Type": "application/json", "Accept": "application/json"}, timeout=10)
        auth_res.raise_for_status()
        auth_token = auth_res.json()["AuthToken"]

        sess_params = {"profile": EBSCO_PROFILE, "guest": "n"}
        if EBSCO_ORG: sess_params["org"] = EBSCO_ORG
        sess_res = requests.get(EBSCO_SESSION_URL, headers={"x-authenticationToken": auth_token, "Accept": "application/json"}, params=sess_params, timeout=10)
        sess_res.raise_for_status()
        session_token = sess_res.json()["SessionToken"]

        headers = {"x-authenticationToken": auth_token, "x-sessionToken": session_token, "Accept": "application/json"}
        search_res = requests.get(EBSCO_SEARCH_URL, headers=headers, params={"query": query, "resultsperpage": max_results}, timeout=15)
        search_res.raise_for_status()
        
        records = search_res.json().get("SearchResult", {}).get("Data", {}).get("Records", [])
        if not records: return t("ไม่พบเปเปอร์ใน EBSCO", "No papers found in EBSCO")

        results = []
        for rec in records[:max_results]:
            items = rec.get("Items", [])
            title = next((i.get("Data", "") for i in items if i.get("Name") == "Title"), "ไม่มีชื่อเรื่อง / No title")
            abstract = next((i.get("Data", "") for i in items if i.get("Name") == "Abstract"), "ไม่มีบทคัดย่อ / No abstract")
            results.append(f"[EBSCO] Title: {_strip_html(title)}\nAbstract: {_strip_html(abstract)[:1000]}...")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error EBSCO: {e}"

def detect_sources(query: str):
    q = query.lower()
    sources = []
    if "pubmed" in q: sources.append("pubmed")
    if "ebsco" in q: sources.append("ebsco")
    if not sources and any(k in q for k in ["หาเปเปอร์", "งานวิจัย", "ค้นหางานวิจัย", "research", "papers"]):
        sources = ["pubmed", "ebsco"]
    return sources

def run_source_search(sources, cleaned_query, max_results=3):
    contexts = []
    for src in sources:
        if src == "pubmed": contexts.append(search_pubmed_stable(cleaned_query, max_results))
        elif src == "ebsco": contexts.append(search_ebsco_stable(cleaned_query, max_results))
    return "\n\n---\n\n".join(contexts)

# ==========================================
# 🤖 5. ระบบ RAG & สมองกล AI (AI Inference)
# ==========================================
def run_ai_routing(prompt, model_name, api_key):
    if not api_key or str(api_key).strip() == "":
        return t("⚠️ [ระบบป้องกันภัย]: ไม่พบ API Key หรือกุญแจถูกระงับ กรุณาตรวจสอบ Secrets ครับ", "⚠️ [Security]: API Key not found or revoked.")

    max_retries = 3
    retry_delay = 2 

    for attempt in range(max_retries):
        try:
            if "Gemini" in model_name:
                os.environ.pop("GOOGLE_API_KEY", None) 
                genai.configure(api_key=api_key.strip())
                model = genai.GenerativeModel(GEMINI_MODEL_MAP.get(model_name, "gemini-1.5-flash"))
                return model.generate_content(prompt).text
                
            elif "GPT" in model_name or "OpenAI" in model_name:
                res = requests.post(
                    "https://api.openai.com/v1/chat/completions", 
                    headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}, 
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}, 
                    timeout=30
                )
                res.raise_for_status()
                return res.json()["choices"][0]["message"]["content"]
                
            else:
                repo_id = HF_MODELS.get(model_name, "SeaLLMs/SeaLLM-7B-v2.5")
                res = requests.post(
                    f"https://api-inference.huggingface.co/models/{repo_id}", 
                    headers={"Authorization": f"Bearer {api_key.strip()}"}, 
                    json={"inputs": prompt, "parameters": {"max_new_tokens": 1000, "temperature": 0.3}}, 
                    timeout=30
                )
                res.raise_for_status() 
                if res.status_code == 200:
                    data = res.json()
                    return data[0].get('generated_text', 'No output') if isinstance(data, list) else str(data)
                return f"HF Error: {res.text}"
                
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue 
            return t("⚠️ ระบบเครือข่ายขัดข้อง โปรดลองใหม่ภายหลัง", "⚠️ Network Error. Please try again later.")
            
        except requests.exceptions.HTTPError as e:
            if "401" in str(e):
                 return t("⚠️ [แจ้งเตือน]: API Key ของท่านถูกปฏิเสธ", "⚠️ [Alert]: Your API Key was unauthorized.")
            return f"API Error: {e}"
            
        except Exception as e:
            return f"Processing Error: {e}"

# ==========================================
# 💻 6. หน้าต่างปฏิบัติการ (Main Workspace)
# ==========================================
def main_app():
    username = st.session_state['username']
    user_role = st.session_state['role']
    
    # 6.1 ตรวจสอบและสร้าง Session
    if not st.session_state.get('current_session_id'):
        c.execute("SELECT session_id FROM chat_sessions WHERE username=? ORDER BY updated_at DESC LIMIT 1", (username,))
        row = c.fetchone()
        if row:
            st.session_state['current_session_id'] = row[0]
        else:
            new_session = str(uuid.uuid4())
            c.execute("INSERT INTO chat_sessions (session_id, username, title) VALUES (?, ?, ?)", (new_session, username, t("แชทใหม่", "New Chat")))
            conn.commit()
            st.session_state['current_session_id'] = new_session

    # 6.2 การตั้งค่าแถบด้านข้าง (Sidebar)
    with st.sidebar:
        # สลับภาษา
        st.session_state['lang'] = st.radio("Language / ภาษา", ["TH", "EN"], horizontal=True)

        user_badge = "👑 Admin" if user_role == 'admin' else ("⭐ Premium" if user_role == 'premium' else "👨‍🔬 User")
        st.header(f"{user_badge}: {username}")
        
        if st.button(t("➕ เพิ่มแชทใหม่", "➕ New Chat"), use_container_width=True, type="primary"):
            new_session = str(uuid.uuid4())
            c.execute("INSERT INTO chat_sessions (session_id, username, title) VALUES (?, ?, ?)", (new_session, username, t("แชทใหม่", "New Chat")))
            conn.commit()
            st.session_state['current_session_id'] = new_session
            st.session_state['messages_loaded'] = False
            st.rerun()

        if st.button(t("ออกจากระบบ", "Logout"), use_container_width=True):
            st.session_state.clear()
            st.rerun()

        # ประวัติแชท
        st.divider()
        st.subheader(t("💬 ประวัติการสนทนา", "💬 Chat History"))
        c.execute("SELECT session_id, title FROM chat_sessions WHERE username=? ORDER BY updated_at DESC", (username,))
        all_sessions = c.fetchall()
        
        for sess_id, title in all_sessions:
            is_active = (sess_id == st.session_state['current_session_id'])
            btn_label = f"🟢 {title}" if is_active else f"📄 {title}"
            if st.button(btn_label, key=f"btn_{sess_id}", use_container_width=True):
                st.session_state['current_session_id'] = sess_id
                st.session_state['messages_loaded'] = False
                st.rerun()

        st.divider()
        st.header(t("⚙️ ตั้งค่ามันสมอง AI", "⚙️ AI Settings"))
        
        # ระบบจำกัดสิทธิ์ (Role Priority)
        access_options = [t("🌟 ฟรี (ส่วนกลาง)", "🌟 Free (Central)")]
        if user_role in ['admin', 'premium']:
            access_options.append(t("🔑 ขั้นสูง (BYOK)", "🔑 Advanced (BYOK)"))
        
        ai_mode = st.radio(t("โหมดการเข้าถึง:", "Access Mode:"), access_options)
        
        selected_model, active_key = None, None
        if ai_mode == t("🌟 ฟรี (ส่วนกลาง)", "🌟 Free (Central)"):
            model_options = list(GEMINI_MODEL_MAP.keys()) + list(HF_MODELS.keys())
            selected_model = st.selectbox(t("เลือก AI:", "Select AI:"), model_options)
            active_key = CENTRAL_GEMINI_KEY if "Gemini" in selected_model else CENTRAL_HF_TOKEN
        else:
            selected_model = st.selectbox(t("เลือกรุ่น Pro (เสียค่าใช้จ่าย):", "Select Pro Model:"), ["Google Gemini 1.5 Pro", "OpenAI GPT-4o"])
            active_key = st.text_input(t("🔑 ใส่ API Key:", "🔑 Enter API Key:"), type="password")

        st.divider()
        st.header(t("📚 แหล่งข้อมูลงานวิจัย", "📚 Research Sources"))
        ebsco_ready = bool(EBSCO_USER_ID and EBSCO_PASSWORD and EBSCO_PROFILE)
        st.write(f"🟢 PubMed: {t('พร้อมใช้งาน', 'Ready') if PUBMED_API_KEY else t('ไม่มี API key', 'No API Key')}")
        st.write(f"{'🟢' if ebsco_ready else '🔴'} EBSCO: {t('พร้อมใช้งาน', 'Ready') if ebsco_ready else t('ยังไม่ได้ตั้งค่า', 'Not Configured')}")

        st.divider()
        st.header(t("🌪️ โหมดวิเคราะห์ลึก", "🌪️ Deep Analysis Mode"))
        use_mini_storm = st.checkbox(t("เปิดใช้งาน Mini STORM Pipeline", "Enable Mini STORM Pipeline"))

 # 6.3 หน้าจอแผงควบคุม Admin
    if user_role == 'admin':
        with st.expander(t("🛠️ แผงควบคุมผู้ดูแลระบบ (Admin Panel)", "🛠️ Admin Control Panel")):
            st.markdown(t("**1. จัดการระดับบัญชีผู้ใช้งาน (Role)**", "**1. Manage User Roles**"))
            
            c.execute("SELECT username, email, role FROM users")
            users_data = c.fetchall()
            df = pd.DataFrame(users_data, columns=["Username", "Email", "Role"])
            
            edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, key="admin_editor")
            
            if st.button(t("💾 บันทึกการเปลี่ยนแปลงสิทธิ์", "💾 Save Role Changes"), type="primary"):
                for index, row in edited_df.iterrows():
                    # ป้องกันไม่ให้แก้สิทธิ์ตัวเองผ่านหน้านี้โดยไม่ตั้งใจ
                    if row['Username'] != username:
                        c.execute("UPDATE users SET role=? WHERE username=?", (row['Role'], row['Username']))
                conn.commit()
                st.success(t("อัปเดตสิทธิ์ผู้ใช้งานสำเร็จ!", "User roles updated successfully!"))

            st.divider()
            
            st.markdown(t("**2. 🚨 จัดการขั้นเด็ดขาด (ลบผู้ใช้ / รีเซ็ตรหัสผ่าน)**", "**2. 🚨 Advanced Actions (Delete / Reset Password)**"))
            
            # ดึงรายชื่อผู้ใช้ทั้งหมด (ยกเว้นแอดมินที่กำลังล็อกอินอยู่ เพื่อป้องกันการเผลอลบตัวเอง)
            target_user = st.selectbox(t("เลือกผู้ใช้งานเป้าหมาย:", "Select Target User:"), [u[0] for u in users_data if u[0] != username])
            
            col1, col2 = st.columns(2)
            with col2:
                new_reset_pwd = st.text_input(t("รหัสผ่านใหม่ (พิมพ์เพื่อเปลี่ยนให้ผู้ใช้นี้):", "New Password (for reset):"), type="password")
                if st.button(t("🔑 บังคับรีเซ็ตรหัสผ่าน", "🔑 Force Reset Password"), use_container_width=True):
                    if target_user and new_reset_pwd:
                        hashed_pwd, salt_hex = hash_password(new_reset_pwd)
                        c.execute("UPDATE users SET password=?, salt=? WHERE username=?", (hashed_pwd, salt_hex, target_user))
                        conn.commit()
                        st.success(f"✅ เปลี่ยนรหัสผ่านใหม่ให้คุณ {target_user} สำเร็จแล้วครับ!")
                    else:
                        st.error("⚠️ กรุณากรอกรหัสผ่านใหม่ที่ต้องการตั้งให้ผู้ใช้นี้ก่อนครับ")

            with col1:
                st.write("") # ดันปุ่มให้ตรงกัน
                st.write("")
                if st.button(t("🗑️ ลบผู้ใช้งานนี้อย่างถาวร", "🗑️ Delete User Permanently"), type="primary", use_container_width=True):
                    if target_user:
                        # ลบข้อมูลผู้ใช้
                        c.execute("DELETE FROM users WHERE username=?", (target_user,))
                        # ลบประวัติแชทและ Session ป้องกันขยะตกค้างในฐานข้อมูล
                        c.execute("DELETE FROM chat_sessions WHERE username=?", (target_user,))
                        c.execute("DELETE FROM chat_history WHERE username=?", (target_user,))
                        conn.commit()
                        st.success(f"🧹 ลบบัญชี {target_user} และประวัติทั้งหมดออกจากระบบเรียบร้อยแล้วครับ!")
                        time.sleep(2)
                        st.rerun()
                st.success(t("อัปเดตสิทธิ์ผู้ใช้งานสำเร็จ!", "User roles updated successfully!"))

    # 6.4 หน้าจอสนทนาหลัก
    st.title(t("🔬 ระบบประมวลผลงานวิจัยอัจฉริยะ", "🔬 Intelligent Research Processing System"))
    current_session = st.session_state['current_session_id']
    
    if not st.session_state['messages_loaded']:
        c.execute("SELECT sender, message FROM chat_history WHERE session_id=? ORDER BY timestamp ASC", (current_session,))
        chat_rows = c.fetchall()
        st.session_state.messages = [{"role": row[0], "content": row[1]} for row in chat_rows]
        st.session_state['messages_loaded'] = True

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if query := st.chat_input(t("พิมพ์คำสั่ง (เช่น 'หาเปเปอร์เกี่ยวกับโปรตีนพืชใน pubmed')", "Enter command (e.g., 'find papers on plant protein in pubmed')")):
        if ai_mode == t("🔑 ขั้นสูง (BYOK)", "🔑 Advanced (BYOK)") and not active_key:
            st.error(t("⚠️ โหมด BYOK บังคับให้ใส่ API Key ส่วนตัวก่อนครับ!", "⚠️ BYOK Mode requires your personal API Key!"))
            return

        c.execute("SELECT title FROM chat_sessions WHERE session_id=?", (current_session,))
        current_title = c.fetchone()[0]
        if current_title in ["แชทใหม่", "New Chat"]:
            new_title = query[:25] + "..." if len(query) > 25 else query
            c.execute("UPDATE chat_sessions SET title=? WHERE session_id=?", (new_title, current_session))
            conn.commit()

        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"): st.markdown(query)
        
        c.execute("INSERT INTO chat_history (session_id, username, sender, message) VALUES (?, ?, ?, ?)", (current_session, username, 'user', query))
        c.execute("UPDATE chat_sessions SET updated_at=CURRENT_TIMESTAMP WHERE session_id=?", (current_session,))
        conn.commit()

        with st.chat_message("assistant"):
            response_ui = ""
            context_text = ""
            sources = detect_sources(query)
            
            if sources:
                with st.spinner(f"🔍 {t('กำลังดึงข้อมูลจาก', 'Fetching data from')} {', '.join(s.upper() for s in sources)}..."):
                    search_kw = query.lower()
                    for junk in ["ใน pubmed", "ใน ebsco", "จาก pubmed", "จาก ebsco", "หาเปเปอร์", "ค้นหางานวิจัย", "in pubmed", "in ebsco"]:
                        search_kw = search_kw.replace(junk, "")
                    context_text = run_source_search(sources, search_kw.strip())
                    response_ui += f"**📖 {t('ข้อมูลอ้างอิงจากฐานข้อมูล:', 'References from Database:')}**\n{context_text}\n\n---\n"
                    st.markdown(response_ui)

            if use_mini_storm and context_text and "Error" not in context_text:
                response_ui += "🌪️ **[Mini STORM Pipeline Initiated]**\n"
                with st.spinner(t("⚙️ กำลังร่างโครงสร้าง (Outline)...", "⚙️ Drafting Outline...")):
                    outline_prompt = t(f"จากบทคัดย่อเหล่านี้ สร้างโครงร่าง 3 หัวข้อหลัก:\n{context_text}", f"From these abstracts, create a 3-point outline:\n{context_text}")
                    outline_result = run_ai_routing(outline_prompt, selected_model, active_key)
                    response_ui += f"**📑 {t('โครงร่างงานวิจัย:', 'Research Outline:')}**\n{outline_result}\n\n"
                    st.markdown(f"**📑 {t('โครงร่างงานวิจัย:', 'Research Outline:')}**\n{outline_result}\n\n")

                with st.spinner(t("⚙️ กำลังเขียนสรุปเชิงลึก...", "⚙️ Writing Deep Summary...")):
                    final_prompt = t(f"จากโครงร่างนี้:\n{outline_result}\n\nจงเขียนสรุปเชิงลึกโดยใช้ข้อมูล:\n{context_text}", f"Based on this outline:\n{outline_result}\n\nWrite a detailed summary using:\n{context_text}")
                    final_result = run_ai_routing(final_prompt, selected_model, active_key)
                    response_ui += f"**📝 {t('บทสรุปเชิงลึก:', 'Detailed Summary:')}**\n{final_result}"
                    st.markdown(f"**📝 {t('บทสรุปเชิงลึก:', 'Detailed Summary:')}**\n{final_result}")
            else:
                with st.spinner(f"🤖 {t('กำลังประมวลผลด้วย', 'Processing with')} {selected_model}..."):
                    ai_prompt = t(f"สรุปข้อมูลและตอบคำถามโดยอิงจากงานวิจัยที่ให้มาเป็นหลัก\n\n[อ้างอิง]:\n{context_text}\n\n[คำถาม]: {query}", f"Summarize and answer based primarily on the provided research.\n\n[Reference]:\n{context_text}\n\n[Question]: {query}") if context_text and "Error" not in context_text and "ไม่พบ" not in context_text else query
                    ai_result = run_ai_routing(ai_prompt, selected_model, active_key)
                    response_ui += f"🤖 **[{t('ผลการวิเคราะห์', 'Analysis Result')}]:**\n{ai_result}"
                    st.markdown(f"🤖 **[{t('ผลการวิเคราะห์', 'Analysis Result')}]:**\n{ai_result}")

            st.session_state.messages.append({"role": "assistant", "content": response_ui})
            c.execute("INSERT INTO chat_history (session_id, username, sender, message) VALUES (?, ?, ?, ?)", (current_session, username, 'assistant', response_ui))
            conn.commit()

# ==========================================
# 🚀 7. ตัวจุดระเบิดระบบ
# ==========================================
if not st.session_state['logged_in']:
    login_register_page()
else:
    main_app()
