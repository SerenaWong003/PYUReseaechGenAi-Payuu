import streamlit as st
import requests
import xml.etree.ElementTree as ET
import time
import re
import os
import hashlib
import binascii
import sqlite3
from urllib.parse import quote
import google.generativeai as genai

# ==========================================
# ⚙️ 1. ตั้งค่าระบบและกุญแจส่วนกลาง
# ==========================================
st.set_page_config(page_title="Payap Research Gen-AI", page_icon="🛡️", layout="wide")

# 🔒 สำคัญ: ไม่ใส่คีย์จริงเป็นค่า fallback ใน st.secrets.get() อีกต่อไป
#    ต้องตั้งค่าใน .streamlit/secrets.toml (local) หรือ "Secrets" ของ Streamlit Cloud เท่านั้น
#    ตัวอย่างไฟล์ secrets.toml:
#    HF_TOKEN = "hf_xxxxx"
#    GEMINI_FREE_KEY = "xxxxx"
#    PUBMED_API_KEY = "xxxxx"
#    EBSCO_USER_ID = "xxxxx"
#    EBSCO_PASSWORD = "xxxxx"
#    EBSCO_PROFILE = "xxxxx"
#    EBSCO_ORG = ""            # ใส่ได้ถ้าสถาบันกำหนดไว้ ไม่บังคับ
#    ADMIN_USERNAME = "xxxxx"  # ไม่บังคับ ใช้สร้างแอดมินครั้งแรกเท่านั้น
#    ADMIN_PASSWORD = "xxxxx"

CENTRAL_HF_TOKEN = st.secrets.get("HF_TOKEN", "")
CENTRAL_GEMINI_KEY = st.secrets.get("GEMINI_FREE_KEY", "")
PUBMED_API_KEY = st.secrets.get("PUBMED_API_KEY", "")

EBSCO_USER_ID = st.secrets.get("EBSCO_USER_ID", "")
EBSCO_PASSWORD = st.secrets.get("EBSCO_PASSWORD", "")
EBSCO_PROFILE = st.secrets.get("EBSCO_PROFILE", "")
EBSCO_ORG = st.secrets.get("EBSCO_ORG", "")

_missing_core = [k for k, v in {
    "GEMINI_FREE_KEY": CENTRAL_GEMINI_KEY,
    "HF_TOKEN": CENTRAL_HF_TOKEN,
    "PUBMED_API_KEY": PUBMED_API_KEY,
}.items() if not v]
if _missing_core:
    st.warning(
        f"⚠️ ยังไม่ได้ตั้งค่า secrets: {', '.join(_missing_core)} — "
        "โหมด 'ฟรี (ส่วนกลาง)' อาจใช้งานไม่ได้จนกว่าจะตั้งค่าใน Secrets ของแอป"
    )

# ฐานข้อมูลโมเดล Hugging Face
HF_MODELS = {
    "SeaLLMs (ภาษาไทย)": "SeaLLMs/SeaLLM-7B-v2.5",
    "Vicuna (ตรรกะ/ทีมเวิร์ค)": "lmsys/vicuna-7b-v1.5",
    "Alpaca (จัดการฟอร์แมตเอกสาร)": "chavinlo/alpaca-native",
    "Gorilla (เขียนโค้ด/เรียก API)": "gorilla-llm/gorilla-7b-hf-v0",
    "ChatGLM (อ่านบริบทยาว)": "THUDM/chatglm3-6b",
}

# แผนที่ชื่อโมเดลที่โชว์ใน UI -> ชื่อโมเดลจริงที่ยิง API (แก้บั๊ก substring matching เดิม)
GEMINI_MODEL_MAP = {
    "Gemini 1.5 Flash": "gemini-1.5-flash",
    "Google Gemini 1.5 Pro": "gemini-1.5-pro",
}

# ==========================================
# 🗄️ 2. ระบบฐานข้อมูลและเข้ารหัสผ่าน (PBKDF2 + salt ต่อผู้ใช้)
# ==========================================
conn = sqlite3.connect('users.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
             (username TEXT PRIMARY KEY, email TEXT, password TEXT, salt TEXT, verified INTEGER)''')
conn.commit()

# migration เผื่อฐานข้อมูลเก่าที่ยังไม่มีคอลัมน์ salt
try:
    c.execute("ALTER TABLE users ADD COLUMN salt TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass  # มีคอลัมน์อยู่แล้ว

PBKDF2_ITERATIONS = 200_000

def hash_password(password: str, salt_hex: str | None = None):
    """คืนค่า (hash_hex, salt_hex). ถ้าไม่ส่ง salt_hex มาจะสุ่ม salt ใหม่ (ใช้ตอนสมัคร)."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return binascii.hexlify(pwd_hash).decode(), binascii.hexlify(salt).decode()

def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    if not stored_salt:
        return False
    check_hash, _ = hash_password(password, stored_salt)
    return check_hash == stored_hash

# ==========================================
# 🔐 3. ระบบยืนยันตัวตน
#    ⚠️ หมายเหตุ: ยังไม่มีการยืนยันอีเมลจริง (ต้องต่อระบบส่งอีเมล/OTP เพิ่มเติม)
#    เหมาะสำหรับ prototype/ใช้งานภายในเท่านั้น
# ==========================================
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'username' not in st.session_state: st.session_state['username'] = ""

def login_register_page():
    st.title("🏛️ Payap Research Gen-AI Hub")
    tab1, tab2 = st.tabs(["เข้าสู่ระบบ", "สมัครสมาชิก"])

    with tab1:
        log_user = st.text_input("ชื่อผู้ใช้", key="log_user")
        log_pwd = st.text_input("รหัสผ่าน", type="password", key="log_pwd")
        if st.button("เข้าสู่ระบบ"):
            c.execute('SELECT password, salt FROM users WHERE username=?', (log_user,))
            user_record = c.fetchone()
            if user_record and verify_password(log_pwd, user_record[0], user_record[1]):
                st.session_state['logged_in'] = True
                st.session_state['username'] = log_user
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
                    'INSERT INTO users (username, email, password, salt, verified) VALUES (?, ?, ?, ?, ?)',
                    (reg_user, reg_email, hashed_pwd, salt_hex, 1)
                )
                conn.commit()
                st.success("✅ สมัครสมาชิกสำเร็จ! สลับไปแท็บ 'เข้าสู่ระบบ' เพื่อล็อกอินได้เลยครับ")
            else:
                st.error("❌ กรุณากรอกข้อมูลให้ครบถ้วน")

# สร้างบัญชีแอดมิน "แบบปลอดภัย" — ทำงานเฉพาะเมื่อผู้ดูแลระบบตั้งค่า ADMIN_USERNAME/ADMIN_PASSWORD
# ไว้ใน secrets เท่านั้น (ไม่ hardcode รหัสผ่านในซอร์สโค้ดอีกต่อไป) และสร้างครั้งเดียวถ้ายังไม่มี
_admin_user = st.secrets.get("ADMIN_USERNAME", "")
_admin_pass = st.secrets.get("ADMIN_PASSWORD", "")
if _admin_user and _admin_pass:
    c.execute('SELECT username FROM users WHERE username=?', (_admin_user,))
    if not c.fetchone():
        _h, _s = hash_password(_admin_pass)
        c.execute(
            'INSERT INTO users (username, email, password, salt, verified) VALUES (?, ?, ?, ?, ?)',
            (_admin_user, f"{_admin_user}@payap.ac.th", _h, _s, 1)
        )
        conn.commit()

# ==========================================
# 📚 4. ระบบดึงฐานข้อมูล (PubMed API)
# ==========================================
def search_pubmed_stable(query, max_results=3):
    safe_query = quote(query)
    base_params = "&tool=PayapGenAI&email=research@payap.ac.th"
    if PUBMED_API_KEY:
        base_params += f"&api_key={PUBMED_API_KEY}"
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&term={safe_query}&retmax={max_results}&retmode=json{base_params}"
    )

    try:
        res = requests.get(url, timeout=10)
        ids = res.json().get('esearchresult', {}).get('idlist', [])
        if not ids:
            return "ไม่พบเปเปอร์ใน PubMed"
        time.sleep(0.2)
        fetch_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=pubmed&id={','.join(ids)}&retmode=xml{base_params}"
        )
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

# ==========================================
# 📚 4.1 ระบบดึงฐานข้อมูล (EBSCO Discovery Service API)
#    อ้างอิงรูปแบบมาตรฐานของ EBSCO EDS API: UIDAuth -> CreateSession -> Search
#    ต้องได้รับ EBSCO_USER_ID / EBSCO_PASSWORD / EBSCO_PROFILE จากห้องสมุด/สถาบันก่อน
#    ⚠️ ถ้าสถาบันของคุณใช้ EBSCO คนละ endpoint/สเปค (เช่น EBSCOhost API อื่น) แจ้งเอกสาร
#       API จริงมาได้ จะปรับให้ตรงครับ
# ==========================================
EBSCO_AUTH_URL = "https://eds-api.ebscohost.com/authservice/rest/UIDAuth"
EBSCO_SESSION_URL = "https://eds-api.ebscohost.com/edsapi/rest/CreateSession"
EBSCO_SEARCH_URL = "https://eds-api.ebscohost.com/edsapi/rest/Search"

def _strip_html(text: str) -> str:
    return re.sub(r"<[^<]+?>", "", text or "")

def _get_ebsco_auth_token():
    payload = {"UserId": EBSCO_USER_ID, "Password": EBSCO_PASSWORD, "Interface": "PayapGenAI"}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    res = requests.post(EBSCO_AUTH_URL, json=payload, headers=headers, timeout=10)
    res.raise_for_status()
    return res.json()["AuthToken"]

def _create_ebsco_session(auth_token):
    headers = {"x-authenticationToken": auth_token, "Accept": "application/json"}
    params = {"profile": EBSCO_PROFILE, "guest": "n"}
    if EBSCO_ORG:
        params["org"] = EBSCO_ORG
    res = requests.get(EBSCO_SESSION_URL, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    return res.json()["SessionToken"]

def search_ebsco_stable(query, max_results=3):
    if not (EBSCO_USER_ID and EBSCO_PASSWORD and EBSCO_PROFILE):
        return "⚠️ ยังไม่ได้ตั้งค่า EBSCO_USER_ID / EBSCO_PASSWORD / EBSCO_PROFILE ใน Secrets"
    try:
        auth_token = _get_ebsco_auth_token()
        session_token = _create_ebsco_session(auth_token)

        headers = {
            "x-authenticationToken": auth_token,
            "x-sessionToken": session_token,
            "Accept": "application/json",
        }
        params = {"query": query, "resultsperpage": max_results}
        res = requests.get(EBSCO_SEARCH_URL, headers=headers, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        records = data.get("SearchResult", {}).get("Data", {}).get("Records", [])
        if not records:
            return "ไม่พบเปเปอร์ใน EBSCO"

        results = []
        for rec in records[:max_results]:
            items = rec.get("Items", [])
            title = next((i.get("Data", "") for i in items if i.get("Name") == "Title"), "ไม่มีชื่อเรื่อง")
            abstract = next((i.get("Data", "") for i in items if i.get("Name") == "Abstract"), "ไม่มีบทคัดย่อ")
            title_clean = _strip_html(title)
            abstract_clean = _strip_html(abstract)[:1000]
            results.append(f"[EBSCO] Title: {title_clean}\nAbstract: {abstract_clean}...")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error EBSCO: {e}"

def detect_sources(query: str):
    """ตรวจจับว่าผู้ใช้ต้องการค้นจากฐานข้อมูลใด จากคำในข้อความ"""
    q = query.lower()
    sources = []
    if "pubmed" in q:
        sources.append("pubmed")
    if "ebsco" in q:
        sources.append("ebsco")
    if not sources and any(k in q for k in ["หาเปเปอร์", "งานวิจัย", "ค้นหางานวิจัย", "literature", "บทความวิชาการ"]):
        sources = ["pubmed", "ebsco"]
    return sources

def run_source_search(sources, cleaned_query, max_results=3):
    contexts = []
    for src in sources:
        if src == "pubmed":
            contexts.append(search_pubmed_stable(cleaned_query, max_results))
        elif src == "ebsco":
            contexts.append(search_ebsco_stable(cleaned_query, max_results))
    return "\n\n---\n\n".join(contexts)

# ==========================================
# 🤖 5. ระบบ RAG & สมองกล AI (AI Inference)
# ==========================================
def ask_gemini(prompt, api_key, model_name):
    try:
        genai.configure(api_key=api_key.strip())
        actual_model = GEMINI_MODEL_MAP.get(model_name, "gemini-1.5-flash")
        model = genai.GenerativeModel(actual_model)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"เกิดข้อผิดพลาดในการประมวลผลของ Gemini: {e}"

def ask_openai(prompt, api_key):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=30)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"เกิดข้อผิดพลาดในการประมวลผลของ OpenAI: {e}"

def ask_huggingface(prompt, model_repo, api_key):
    url = f"https://api-inference.huggingface.co/models/{model_repo}"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 1000, "temperature": 0.3}}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                return result[0].get('generated_text', 'ไม่สามารถสร้างข้อความได้')
            return str(result)
        return f"HF Error (อาจต้องรอโมเดลโหลดสักครู่): {response.text}"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"

def run_ai_routing(prompt, model_name, api_key):
    # แก้บั๊กเดิม: ตอนเลือก "OpenAI GPT-4o" โค้ดเก่าจะหลุดไปเรียก Hugging Face โดยไม่ตั้งใจ
    if "Gemini" in model_name:
        return ask_gemini(prompt, api_key, model_name)
    elif "GPT" in model_name or "OpenAI" in model_name:
        return ask_openai(prompt, api_key)
    else:
        repo_id = HF_MODELS.get(model_name, "SeaLLMs/SeaLLM-7B-v2.5")
        return ask_huggingface(prompt, repo_id, api_key)

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
            model_options = list(GEMINI_MODEL_MAP.keys())[:1] + list(HF_MODELS.keys())
            selected_model = st.selectbox("เลือก AI:", model_options)
            active_key = CENTRAL_GEMINI_KEY if "Gemini" in selected_model else CENTRAL_HF_TOKEN
        else:
            selected_model = st.selectbox("เลือกรุ่น Pro (เสียค่าใช้จ่าย):", ["Google Gemini 1.5 Pro", "OpenAI GPT-4o"])
            active_key = st.text_input("🔑 ใส่ API Key:", type="password")

        st.divider()
        st.header("📚 แหล่งข้อมูลงานวิจัย")
        st.caption("ระบบจะเลือกแหล่งข้อมูลอัตโนมัติจากคำในข้อความ (เช่น 'pubmed', 'ebsco') หรือคำทั่วไปเช่น 'หาเปเปอร์'")
        ebsco_ready = bool(EBSCO_USER_ID and EBSCO_PASSWORD and EBSCO_PROFILE)
        st.write(f"🟢 PubMed: {'พร้อมใช้งาน' if PUBMED_API_KEY else 'ไม่มี API key (ยังค้นได้แบบไม่มีคีย์)'}")
        st.write(f"{'🟢' if ebsco_ready else '🔴'} EBSCO: {'พร้อมใช้งาน' if ebsco_ready else 'ยังไม่ได้ตั้งค่า Secrets'}")

        st.divider()
        st.header("🌪️ โหมดวิเคราะห์ลึก")
        use_mini_storm = st.checkbox("เปิดใช้งาน Mini STORM Pipeline", help="AI จะทำงาน 3 ขั้นตอน: ค้นหา -> ร่างโครง -> สรุปเชิงลึก")

    st.title("🔬 ระบบประมวลผลงานวิจัยอัจฉริยะ")
    if "messages" not in st.session_state: st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if query := st.chat_input("พิมพ์คำสั่ง (เช่น 'หาเปเปอร์เกี่ยวกับโปรตีนพืชใน pubmed หรือ ebsco')"):
        if ai_mode == "🔑 ขั้นสูง (BYOK)" and not active_key:
            st.error("⚠️ โหมด BYOK บังคับให้ใส่ API Key ส่วนตัวก่อนครับ!")
            return

        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"): st.markdown(query)

        with st.chat_message("assistant"):
            response_ui = ""
            context_text = ""

            # 1. การดึงข้อมูล (Retrieval) — รองรับทั้ง PubMed และ EBSCO
            sources = detect_sources(query)
            if sources:
                with st.spinner(f"🔍 กำลังดึงข้อมูลจาก {', '.join(s.upper() for s in sources)}..."):
                    search_kw = query.lower()
                    for junk in ["ใน pubmed", "ใน ebsco", "จาก pubmed", "จาก ebsco", "หาเปเปอร์", "ค้นหางานวิจัย"]:
                        search_kw = search_kw.replace(junk, "")
                    search_kw = search_kw.strip()
                    context_text = run_source_search(sources, search_kw)
                    response_ui += f"**📖 ข้อมูลอ้างอิงจากฐานข้อมูล:**\n{context_text}\n\n---\n"
                    st.markdown(response_ui)

            # 2. การประมวลผล (Inference Pipeline)
            if use_mini_storm and context_text and "Error" not in context_text:
                response_ui += "🌪️ **[Mini STORM Pipeline Initiated]**\n"

                with st.spinner("⚙️ Agent 1: กำลังสังเคราะห์ข้อมูลและร่างโครงสร้าง (Outline)..."):
                    outline_prompt = f"จากบทคัดย่อเหล่านี้ กรุณาสร้างโครงร่าง (Outline) 3 หัวข้อหลักสำหรับการเขียนบทความวิจัย:\n{context_text}"
                    outline_result = run_ai_routing(outline_prompt, selected_model, active_key)
                    response_ui += f"**📑 โครงร่างงานวิจัย (Outline):**\n{outline_result}\n\n"
                    st.markdown(response_ui)

                with st.spinner("⚙️ Agent 2: กำลังขยายความและเขียนสรุปเชิงลึก..."):
                    draft_prompt = f"จากโครงร่างนี้:\n{outline_result}\n\nจงเขียนสรุปเชิงลึกโดยใช้ข้อมูลจากบทคัดย่อต่อไปนี้:\n{context_text}"
                    final_result = run_ai_routing(draft_prompt, selected_model, active_key)
                    response_ui += f"**📝 บทสรุปเชิงลึก (Deep Summary):**\n{final_result}"
                    st.markdown(response_ui)

            else:
                with st.spinner(f"🤖 กำลังประมวลผลด้วย {selected_model}..."):
                    ai_prompt = query
                    if context_text and "Error" not in context_text and "ไม่พบ" not in context_text:
                        ai_prompt = f"สรุปข้อมูลและตอบคำถามโดยอิงจากงานวิจัยที่ให้มาเป็นหลัก\n\n[ข้อมูลอ้างอิง]:\n{context_text}\n\n[คำถาม]: {query}"
                    ai_result = run_ai_routing(ai_prompt, selected_model, active_key)
                    response_ui += f"🤖 **[ผลการวิเคราะห์]:**\n{ai_result}"
                    st.markdown(response_ui)

            st.session_state.messages.append({"role": "assistant", "content": response_ui})

# ==========================================
# 🚀 7. ตัวจุดระเบิดระบบ
# ==========================================
if not st.session_state['logged_in']:
    login_register_page()
else:
    main_app()
