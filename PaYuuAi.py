import streamlit as st
import requests
import xml.etree.ElementTree as ET
import time
from urllib.parse import quote
import sqlite3
import hashlib

# ==========================================
# ⚙️ 1. ตั้งค่าระบบและกุญแจส่วนกลาง 
# ==========================================
st.set_page_config(page_title="Payap Research Gen-AI", page_icon="🛡️", layout="wide")

CENTRAL_HF_TOKEN = st.secrets.get("HF_TOKEN", "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")
CENTRAL_GEMINI_KEY = st.secrets.get("GEMINI_FREE_KEY", "AQ.Ab8RN6JKAkLy3QUC0ZlMXlADfCcpZ_u0Q5e_FKNdfzIbprlHVw")
PUBMED_API_KEY = st.secrets.get("PUBMED_API_KEY", "55ca775dbcce505de81e116837ccbff61709")

# ฐานข้อมูลโมเดล Hugging Face
HF_MODELS = {
    "SeaLLMs (ภาษาไทย)": "SeaLLMs/SeaLLM-7B-v2.5",
    "Vicuna (ตรรกะ/ทีมเวิร์ค)": "lmsys/vicuna-7b-v1.5",
    "Alpaca (จัดการฟอร์แมตเอกสาร)": "chavinlo/alpaca-native",
    "Gorilla (เขียนโค้ด/เรียก API)": "gorilla-llm/gorilla-7b-hf-v0",
    "ChatGLM (อ่านบริบทยาว)": "THUDM/chatglm3-6b"
}

# ==========================================
# 🗄️ 2. ระบบฐานข้อมูลและเข้ารหัสผ่าน
# ==========================================
conn = sqlite3.connect('users.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, email TEXT, password TEXT, verified INTEGER)''')
conn.commit()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# 🔐 3. ระบบยืนยันตัวตน (Bypass Email)
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
            hashed_pwd = hash_password(log_pwd)
            c.execute('SELECT password FROM users WHERE username=?', (log_user,))
            user_record = c.fetchone()
            if user_record and user_record[0] == hashed_pwd:
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
                hashed_pwd = hash_password(reg_pwd)
                c.execute('INSERT INTO users (username, email, password, verified) VALUES (?, ?, ?, 1)', 
                          (reg_user, reg_email, hashed_pwd))
                conn.commit()
                st.success("✅ สมัครสมาชิกสำเร็จ! สลับไปแท็บ 'เข้าสู่ระบบ' เพื่อล็อกอินได้เลยครับ")
            else:
                st.error("❌ กรุณากรอกข้อมูลให้ครบถ้วน")

# ==========================================
# 📚 4. ระบบดึงฐานข้อมูล (PubMed API)
# ==========================================
def search_pubmed_stable(query, max_results=3):
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
            abs_text = abs_tag.text[:1000] + "..." if abs_tag is not None and abs_tag.text else "ไม่มีบทคัดย่อ"
            results.append(f"Title: {title_text}\nAbstract: {abs_text}")
        return "\n\n".join(results)
    except Exception as e:
        return f"Error PubMed: {e}"

# ==========================================
# 🤖 5. ระบบ RAG & สมองกล AI (AI Inference)
# ==========================================
def ask_gemini(prompt, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        return f"Gemini Error: {response.text}"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"

def ask_huggingface(prompt, model_repo, api_key):
    url = f"https://api-inference.huggingface.co/models/{model_repo}"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 1000, "temperature": 0.3}}
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                return result[0].get('generated_text', 'ไม่สามารถสร้างข้อความได้')
            return str(result)
        return f"HF Error (อาจต้องรอโมเดลโหลดสักครู่): {response.text}"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"

def run_ai_routing(prompt, model_name, api_key):
    if "Gemini" in model_name:
        return ask_gemini(prompt, api_key)
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
            model_options = ["Gemini 1.5 Flash"] + list(HF_MODELS.keys())
            selected_model = st.selectbox("เลือก AI:", model_options)
            active_key = CENTRAL_GEMINI_KEY if "Gemini" in selected_model else CENTRAL_HF_TOKEN
        else:
            selected_model = st.selectbox("เลือกรุ่น Pro (เสียค่าใช้จ่าย):", ["Google Gemini 1.5 Pro", "OpenAI GPT-4o"])
            active_key = st.text_input("🔑 ใส่ API Key:", type="password")
            
        st.divider()
        st.header("🌪️ โหมดวิเคราะห์ลึก")
        use_mini_storm = st.checkbox("เปิดใช้งาน Mini STORM Pipeline", help="AI จะทำงาน 3 ขั้นตอน: ค้นหา -> ร่างโครง -> สรุปเชิงลึก (ใช้เวลาประมวลผลนานขึ้น)")

    st.title("🔬 ระบบประมวลผลงานวิจัยอัจฉริยะ")
    if "messages" not in st.session_state: st.session_state.messages = []
    
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if query := st.chat_input("พิมพ์คำสั่ง (เช่น 'หาเปเปอร์เกี่ยวกับโปรตีนพืชใน pubmed')"):
        if ai_mode == "🔑 ขั้นสูง (BYOK)" and not active_key:
            st.error("⚠️ โหมด BYOK บังคับให้ใส่ API Key ส่วนตัวก่อนครับ!")
            return

        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"): st.markdown(query)

        with st.chat_message("assistant"):
            response_ui = ""
            context_text = ""
            
            # 1. การดึงข้อมูล (Retrieval)
            if "pubmed" in query.lower():
                with st.spinner("🔍 กำลังดึงข้อมูลจากฐาน PubMed..."):
                    search_kw = query.lower().replace("ใน pubmed", "").replace("หาเปเปอร์", "").strip()
                    context_text = search_pubmed_stable(search_kw)
                    response_ui += f"**📖 ข้อมูลอ้างอิงจาก PubMed:**\n{context_text}\n\n---\n"
                    st.markdown(response_ui)

            # 2. การประมวลผล (Inference Pipeline)
            if use_mini_storm and context_text and "Error" not in context_text:
                response_ui += "🌪️ **[Mini STORM Pipeline Initiated]**\n"
                
                # Step 1: ให้ AI ร่างโครงสร้าง
                with st.spinner("⚙️ Agent 1: กำลังสังเคราะห์ข้อมูลและร่างโครงสร้าง (Outline)..."):
                    outline_prompt = f"จากบทคัดย่อเหล่านี้ กรุณาสร้างโครงร่าง (Outline) 3 หัวข้อหลักสำหรับการเขียนบทความวิจัย:\n{context_text}"
                    outline_result = run_ai_routing(outline_prompt, selected_model, active_key)
                    response_ui += f"**📑 โครงร่างงานวิจัย (Outline):**\n{outline_result}\n\n"
                    st.markdown(response_ui)
                
                # Step 2: ให้ AI เขียนสรุปตามโครงสร้าง
                with st.spinner("⚙️ Agent 2: กำลังขยายความและเขียนสรุปเชิงลึก..."):
                    draft_prompt = f"จากโครงร่างนี้:\n{outline_result}\n\nจงเขียนสรุปเชิงลึกโดยใช้ข้อมูลจากบทคัดย่อต่อไปนี้:\n{context_text}"
                    final_result = run_ai_routing(draft_prompt, selected_model, active_key)
                    response_ui += f"**📝 บทสรุปเชิงลึก (Deep Summary):**\n{final_result}"
                    st.markdown(response_ui)
            
            else:
                # การทำงานแบบปกติ (Single Prompt)
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
if not st.session_state['logged_in']: login_register_page()
else: main_app()
def ask_gemini(prompt, api_key, model_name):
    # 🛡️ ฝังกุญแจของนายหญิงลงไปตรงนี้โดยตรง (ลบคำว่า AIzaSy_... แล้วใส่กุญแจจริงของนายหญิง)
    hardcoded_key = "AIzaSy_ใส่กุญแจของนายหญิงที่นี่"
    
    actual_model = "gemini-1.5-pro" if "Pro" in model_name else "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{actual_model}:generateContent?key={hardcoded_key}"
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        return f"Gemini Error ({response.status_code}): {response.text}"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"
def ask_huggingface(prompt, model_repo, api_key):
    # (คงโค้ดเดิมของ ask_huggingface ไว้)
    url = f"https://api-inference.huggingface.co/models/{model_repo}"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 1000, "temperature": 0.3}}
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                return result[0].get('generated_text', 'ไม่สามารถสร้างข้อความได้')
            return str(result)
        return f"HF Error (อาจต้องรอโมเดลโหลดสักครู่): {response.text}"
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}"

def run_ai_routing(prompt, model_name, api_key):
    if "Gemini" in model_name:
        # ส่งชื่อโมเดลเข้าไปให้ระบบใหม่จัดการ
        return ask_gemini(prompt, api_key, model_name)
    else:
        repo_id = HF_MODELS.get(model_name, "SeaLLMs/SeaLLM-7B-v2.5")
        return ask_huggingface(prompt, repo_id, api_key)
