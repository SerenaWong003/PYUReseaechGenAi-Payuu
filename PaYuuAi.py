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
<script src="https://apis.google.com/js/platform.js" async defer></script>
<div class="g-signin2" data-onsuccess="onSignIn"></div>
function onSignIn(googleUser) {
  var profile = googleUser.getBasicProfile();
  console.log('ID: ' + profile.getId()); // Do not send to your backend! Use an ID token instead.
  console.log('Name: ' + profile.getName());
  console.log('Image URL: ' + profile.getImageUrl());
  console.log('Email: ' + profile.getEmail()); // This is null if the 'email' scope is not present.
}
<a href="#" onclick="signOut();">Sign out</a>
<script>
  function signOut() {
    var auth2 = gapi.auth2.getAuthInstance();
    auth2.signOut().then(function () {
      console.log('User signed out.');
    });
  }
</script>
# ==========================================
# ⚙️ 1. ตั้งค่าระบบและกุญแจส่วนกลาง
# ==========================================
st.set_page_config(page_title="Payap Research Gen-AI", page_icon="🛡️", layout="wide")

CENTRAL_HF_TOKEN = st.secrets.get("HF_TOKEN","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")
CENTRAL_GEMINI_KEY = st.secrets.get("GEMINI_FREE_KEY","AQ.Ab8RN6IrVY1lgOHmkIGuSLzExFFyD3HSq6KHl0PHhvtj0T40GQ")
PUBMED_API_KEY = st.secrets.get("PUBMED_API_KEY","55ca775dbcce505de81e116837ccbff61709")

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
    "SeaLLMs (ภาษาไทย)": "SeaLLMs/SeaLLM-7B-v2.5",
    "Vicuna (ตรรกะ/ทีมเวิร์ค)": "lmsys/vicuna-7b-v1.5",
    "Alpaca (จัดการฟอร์แมต)": "chavinlo/alpaca-native",
    "Gorilla (เขียนโค้ด/API)": "gorilla-llm/gorilla-7b-hf-v0",
    "ChatGLM (อ่านบริบทยาว)": "THUDM/chatglm3-6b",
}

GEMINI_MODEL_MAP = {
    "Gemini 1.5 Flash": "gemini-1.5-flash",
    "Google Gemini 1.5 Pro": "gemini-1.5-pro",
}

# ==========================================
# 🗄️ 2. ระบบฐานข้อมูล v3 (รองรับ Multiple Chat Sessions)
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
    st.title("🏛️ Payap Research Gen-AI Hub")
    tab1, tab2 = st.tabs(["เข้าสู่ระบบ", "สมัครสมาชิก"])

    with tab1:
        log_user = st.text_input("ชื่อผู้ใช้", key="log_user")
        log_pwd = st.text_input("รหัสผ่าน", type="password", key="log_pwd")
        if st.button("เข้าสู่ระบบ"):
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
                st.error("❌ ข้อมูลไม่ถูกต้อง")

    with tab2:
        reg_user = st.text_input("ชื่อผู้ใช้ใหม่")
        reg_email = st.text_input("อีเมล")
        reg_pwd = st.text_input("รหัสผ่าน", type="password")
        if st.button("สมัครสมาชิก (เข้าระบบทันที)"):
            c.execute('SELECT username FROM users WHERE username=?', (reg_user,))
            if c.fetchone():
                st.warning("⚠️ ชื่อผู้ใช้นี้มีในระบบแล้ว กรุณาใช้ชื่ออื่นครับ")
            elif reg_user and reg_pwd:
                hashed_pwd, salt_hex = hash_password(reg_pwd)
                c.execute(
                    'INSERT INTO users (username, email, password, salt, verified, role) VALUES (?, ?, ?, ?, ?, ?)',
                    (reg_user, reg_email, hashed_pwd, salt_hex, 1, 'user')
                )
                conn.commit()
                st.success("✅ สมัครสมาชิกสำเร็จ! สลับไปแท็บ 'เข้าสู่ระบบ' เพื่อล็อกอินได้เลยครับ")
            else:
                st.error("❌ กรุณากรอกข้อมูลให้ครบถ้วน")

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
def search_pubmed_stable(query, max_results=3):
    safe_query = quote(query)
    base_params = "&tool=PayapGenAI&email=research@payap.ac.th"
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
            abs_text = abs_tag.text[:1000] + "..." if abs_tag is not None and abs_tag.text else "ไม่มีบทคัดย่อ"
            results.append(f"[PubMed] Title: {title_text}\nAbstract: {abs_text}")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error PubMed: {e}"

def search_ebsco_stable(query, max_results=3):
    if not (EBSCO_USER_ID and EBSCO_PASSWORD and EBSCO_PROFILE):
        return "⚠️ ยังไม่ได้ตั้งค่า EBSCO Secrets"
    
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
        if not records: return "ไม่พบเปเปอร์ใน EBSCO"

        results = []
        for rec in records[:max_results]:
            items = rec.get("Items", [])
            title = next((i.get("Data", "") for i in items if i.get("Name") == "Title"), "ไม่มีชื่อเรื่อง")
            abstract = next((i.get("Data", "") for i in items if i.get("Name") == "Abstract"), "ไม่มีบทคัดย่อ")
            results.append(f"[EBSCO] Title: {_strip_html(title)}\nAbstract: {_strip_html(abstract)[:1000]}...")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error EBSCO: {e}"

def detect_sources(query: str):
    q = query.lower()
    sources = []
    if "pubmed" in q: sources.append("pubmed")
    if "ebsco" in q: sources.append("ebsco")
    if not sources and any(k in q for k in ["หาเปเปอร์", "งานวิจัย", "ค้นหางานวิจัย"]):
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
import time

def run_ai_routing(prompt, model_name, api_key):
    # 🛡️ ดักจับกุญแจว่างเปล่า
    if not api_key or str(api_key).strip() == "":
        return "⚠️ [ระบบป้องกันภัย]: ไม่พบ API Key หรือกุญแจถูกระงับ กรุณาตรวจสอบ Secrets ครับ"

    max_retries = 3
    retry_delay = 2 # หน่วงเวลา 2 วินาทีก่อนลองใหม่

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
                res.raise_for_status() # ดัก Error 401 หรือ 500 ทันที
                if res.status_code == 200:
                    data = res.json()
                    return data[0].get('generated_text', 'No output') if isinstance(data, list) else str(data)
                return f"HF Error: {res.text}"
                
        # ดักจับปัญหาอินเทอร์เน็ตหลุด / หาเซิร์ฟเวอร์ไม่เจอ (NameResolutionError)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue # ลองวนลูปใหม่
            return f"⚠️ ระบบเครือข่ายขัดข้อง: ไม่สามารถเชื่อมต่อกับ {model_name} ได้หลังจากพยายาม {max_retries} ครั้ง โปรดลองใหม่ภายหลังครับ"
            
        # ดักจับปัญหาอื่นๆ เช่น กุญแจพัง กุญแจหมดอายุ (401 Unauthorized)
        except requests.exceptions.HTTPError as e:
            if "401" in str(e):
                 return "⚠️ [แจ้งเตือนความปลอดภัย]: API Key ของท่านถูกปฏิเสธ (อาจถูกเพิกถอนเนื่องจากหลุดไปบนอินเทอร์เน็ต) โปรดเปลี่ยนกุญแจใหม่ครับ"
            return f"เกิดข้อผิดพลาดจากเซิร์ฟเวอร์ API: {e}"
            
        except Exception as e:
            return f"เกิดข้อผิดพลาดในการประมวลผล: {e}"

# ==========================================
# 💻 6. หน้าต่างปฏิบัติการ (Main Workspace)
# ==========================================
def main_app():
    username = st.session_state['username']
    
    # 6.1 ตรวจสอบและสร้าง Session เริ่มต้น
    if not st.session_state.get('current_session_id'):
        c.execute("SELECT session_id FROM chat_sessions WHERE username=? ORDER BY updated_at DESC LIMIT 1", (username,))
        row = c.fetchone()
        if row:
            st.session_state['current_session_id'] = row[0]
        else:
            new_session = str(uuid.uuid4())
            c.execute("INSERT INTO chat_sessions (session_id, username, title) VALUES (?, ?, ?)", (new_session, username, "แชทใหม่"))
            conn.commit()
            st.session_state['current_session_id'] = new_session

    # 6.2 การตั้งค่าแถบด้านข้าง (Sidebar)
    with st.sidebar:
        user_badge = "👑 ผู้ดูแลระบบ" if st.session_state['role'] == 'admin' else "👨‍🔬 นักวิจัย"
        st.header(f"{user_badge}: {username}")
        
        if st.button("➕ เพิ่มแชทใหม่", use_container_width=True, type="primary"):
            new_session = str(uuid.uuid4())
            c.execute("INSERT INTO chat_sessions (session_id, username, title) VALUES (?, ?, ?)", (new_session, username, "แชทใหม่"))
            conn.commit()
            st.session_state['current_session_id'] = new_session
            st.session_state['messages_loaded'] = False
            st.rerun()

        if st.button("ออกจากระบบ", use_container_width=True):
            st.session_state.clear()
            st.rerun()

        # แสดงรายการประวัติการสนทนาทั้งหมด
        st.divider()
        st.subheader("💬 ประวัติการสนทนา")
        c.execute("SELECT session_id, title FROM chat_sessions WHERE username=? ORDER BY updated_at DESC", (username,))
        all_sessions = c.fetchall()
        
        for sess_id, title in all_sessions:
            is_active = (sess_id == st.session_state['current_session_id'])
            btn_label = f"🟢 {title}" if is_active else f"📄 {title}"
            if st.button(btn_label, key=f"btn_{sess_id}", use_container_width=True):
                st.session_state['current_session_id'] = sess_id
                st.session_state['messages_loaded'] = False
                st.rerun()

        if st.session_state['role'] == 'admin':
            st.divider()
            st.subheader("🛠️ แผงควบคุม (Admin Panel)")
            c.execute('SELECT COUNT(*) FROM users')
            user_count = c.fetchone()[0]
            st.info(f"ผู้ใช้งานในระบบทั้งหมด: {user_count} บัญชี")

        st.divider()
        st.header("⚙️ ตั้งค่ามันสมอง AI")
        ai_mode = st.radio("โหมดการเข้าถึง:", ["🌟 ฟรี (ส่วนกลาง)", "🔑 ขั้นสูง (BYOK)"])
        selected_model, active_key = None, None
        if ai_mode == "🌟 ฟรี (ส่วนกลาง)":
            model_options = list(GEMINI_MODEL_MAP.keys()) + list(HF_MODELS.keys())
            selected_model = st.selectbox("เลือก AI:", model_options)
            active_key = CENTRAL_GEMINI_KEY if "Gemini" in selected_model else CENTRAL_HF_TOKEN
        else:
            selected_model = st.selectbox("เลือกรุ่น Pro (เสียค่าใช้จ่าย):", ["Google Gemini 1.5 Pro", "OpenAI GPT-4o"])
            active_key = st.text_input("🔑 ใส่ API Key:", type="password")

        st.divider()
        st.header("📚 แหล่งข้อมูลงานวิจัย")
        ebsco_ready = bool(EBSCO_USER_ID and EBSCO_PASSWORD and EBSCO_PROFILE)
        st.write(f"🟢 PubMed: {'พร้อมใช้งาน' if PUBMED_API_KEY else 'ไม่มี API key'}")
        st.write(f"{'🟢' if ebsco_ready else '🔴'} EBSCO: {'พร้อมใช้งาน' if ebsco_ready else 'ยังไม่ได้ตั้งค่า'}")

        st.divider()
        st.header("🌪️ โหมดวิเคราะห์ลึก")
        use_mini_storm = st.checkbox("เปิดใช้งาน Mini STORM Pipeline")

    # 6.3 หน้าจอสนทนาหลัก
    st.title("🔬 ระบบประมวลผลงานวิจัยอัจฉริยะ")
    current_session = st.session_state['current_session_id']
    
    if not st.session_state['messages_loaded']:
        c.execute("SELECT sender, message FROM chat_history WHERE session_id=? ORDER BY timestamp ASC", (current_session,))
        chat_rows = c.fetchall()
        st.session_state.messages = [{"role": row[0], "content": row[1]} for row in chat_rows]
        st.session_state['messages_loaded'] = True

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if query := st.chat_input("พิมพ์คำสั่ง (เช่น 'หาเปเปอร์เกี่ยวกับโปรตีนพืชใน pubmed')"):
        if ai_mode == "🔑 ขั้นสูง (BYOK)" and not active_key:
            st.error("⚠️ โหมด BYOK บังคับให้ใส่ API Key ส่วนตัวก่อนครับ!")
            return

        # อัปเดตชื่อแชทอัตโนมัติหากยังเป็น "แชทใหม่"
        c.execute("SELECT title FROM chat_sessions WHERE session_id=?", (current_session,))
        current_title = c.fetchone()[0]
        if current_title == "แชทใหม่":
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
                with st.spinner(f"🔍 กำลังดึงข้อมูลจาก {', '.join(s.upper() for s in sources)}..."):
                    search_kw = query.lower()
                    for junk in ["ใน pubmed", "ใน ebsco", "จาก pubmed", "จาก ebsco", "หาเปเปอร์", "ค้นหางานวิจัย"]:
                        search_kw = search_kw.replace(junk, "")
                    context_text = run_source_search(sources, search_kw.strip())
                    response_ui += f"**📖 ข้อมูลอ้างอิงจากฐานข้อมูล:**\n{context_text}\n\n---\n"
                    st.markdown(response_ui)

            if use_mini_storm and context_text and "Error" not in context_text:
                response_ui += "🌪️ **[Mini STORM Pipeline Initiated]**\n"
                with st.spinner("⚙️ กำลังร่างโครงสร้าง (Outline)..."):
                    outline_result = run_ai_routing(f"จากบทคัดย่อเหล่านี้ สร้างโครงร่าง 3 หัวข้อหลัก:\n{context_text}", selected_model, active_key)
                    response_ui += f"**📑 โครงร่างงานวิจัย:**\n{outline_result}\n\n"
                    st.markdown(f"**📑 โครงร่างงานวิจัย:**\n{outline_result}\n\n")

                with st.spinner("⚙️ กำลังเขียนสรุปเชิงลึก..."):
                    final_result = run_ai_routing(f"จากโครงร่างนี้:\n{outline_result}\n\nจงเขียนสรุปเชิงลึกโดยใช้ข้อมูล:\n{context_text}", selected_model, active_key)
                    response_ui += f"**📝 บทสรุปเชิงลึก:**\n{final_result}"
                    st.markdown(f"**📝 บทสรุปเชิงลึก:**\n{final_result}")
            else:
                with st.spinner(f"🤖 กำลังประมวลผลด้วย {selected_model}..."):
                    ai_prompt = f"สรุปข้อมูลและตอบคำถามโดยอิงจากงานวิจัยที่ให้มาเป็นหลัก\n\n[อ้างอิง]:\n{context_text}\n\n[คำถาม]: {query}" if context_text and "Error" not in context_text and "ไม่พบ" not in context_text else query
                    ai_result = run_ai_routing(ai_prompt, selected_model, active_key)
                    response_ui += f"🤖 **[ผลการวิเคราะห์]:**\n{ai_result}"
                    st.markdown(f"🤖 **[ผลการวิเคราะห์]:**\n{ai_result}")

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
