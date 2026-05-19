# -*- coding: utf-8 -*-
"""数字主持人 - Flask版本（对话记忆+持久化 + 单条TTS + 双通道消息）"""
import os, sys, io
import tempfile
import uuid
import asyncio
import edge_tts
import re
import json
import requests
import urllib3
from flask import Flask, request, jsonify, send_file, Response
from threading import Lock
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

PORT = 8767
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

_config = {"base_url": "", "api_key": "", "model": ""}
_config_lock = Lock()


def load_config():
    global _config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                _config = json.load(f)
            safe_print(f"[config] loaded from {CONFIG_FILE}")
        except Exception as e:
            safe_print(f"[config] load error: {e}")


def save_config(data):
    global _config
    with _config_lock:
        _config = {
            "base_url": data.get("base_url", "").strip().rstrip("/"),
            "api_key": data.get("api_key", "").strip(),
            "model": data.get("model", "").strip(),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False, indent=2)
    safe_print(f"[config] saved")


load_config()

SYSTEM_PROMPT = """你是一个直播间数字主持人，名叫"芥末"，是一只可爱的小龙虾形象。你活泼开朗，说话直接有趣，像一个真实的主播。
要求：
- 每次回复不超过100字
- 语气活泼热情，像真的在和观众互动
- 不要重复开场白
- 如果观众问你是谁，可以介绍一下自己是数字主持人
- 多用"朋友们"称呼"""

# ===== TTS 当前音频（AI回答）=====
_current_audio = {"file": None, "id": None, "done": False}
_tts_lock = Lock()

# ===== TTS 用户消息音频 ======
_current_user_tts = {"file": None, "id": None, "done": False}
_user_tts_lock = Lock()

# ===== 对话历史持久化（JSON文件）=====
HISTORY_FILE = os.path.join(SCRIPT_DIR, "chat_history.json")
chat_history = defaultdict(list)
history_lock = Lock()


def safe_print(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _load_history():
    """从JSON文件加载对话历史"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                chat_history[k] = v
            total = sum(len(v) for v in data.values())
            safe_print(f"[持久化] 加载 {len(data)} 个会话，共 {total} 条消息")
        except Exception as e:
            safe_print(f"[持久化] 加载失败: {e}")


def _save_history():
    """将对话历史保存到JSON文件（线程安全）"""
    try:
        with history_lock:
            data = dict(chat_history)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        safe_print(f"[持久化] 保存失败: {e}")


# 启动时加载历史
_load_history()


# ===== 远程消息队列（飞书等外部渠道的问题+回答，浏览器轮询用）=====
_msg_queue = []  # 每个元素: {"type": "user"/"ai", "text": str, "id": str, "tts_id": str}
_queue_lock = Lock()

urllib3.disable_warnings()

def clean_for_tts(text):
    text = re.sub(r'\*{2,}(.+?)\*{2,}', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^\u003e+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

@app.after_request
def after_request(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/api/config', methods=['GET'])
def get_config():
    key = _config.get("api_key", "")
    masked = key[:8] + "..." + key[-4:] if len(key) > 12 else ("***" if key else "")
    return jsonify({
        "base_url": _config.get("base_url", ""),
        "api_key_masked": masked,
        "has_key": bool(key),
        "model": _config.get("model", ""),
    })


@app.route('/api/config', methods=['POST'])
def post_config():
    data = request.get_json() or {}
    save_config(data)
    return jsonify({"ok": True})


@app.route('/')
def index():
    idx = os.path.join(SCRIPT_DIR, "index.html")
    if os.path.exists(idx):
        with open(idx, "rb") as f:
            return Response(f.read(), mimetype="text/html; charset=utf-8")
    return "index.html not found", 404


@app.route('/<path:filename>')
def static_files(filename):
    filepath = os.path.join(SCRIPT_DIR, filename)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        ext = os.path.splitext(filename)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".js": "application/javascript", ".css": "text/css"}
        mime = mime_map.get(ext, "application/octet-stream")
        with open(filepath, "rb") as f:
            return Response(f.read(), mimetype=mime)
    return "Not found", 404


# ===== 远程轮询接口（飞书等消息走这里，浏览器每2秒轮询）=====
@app.route('/poll')
def poll():
    """按顺序返回队列中的消息（远程调用产生的用户问题/AI回答）"""
    last_id = request.args.get('last_id', '')
    with _queue_lock:
        for i, msg in enumerate(_msg_queue):
            if msg['id'] != last_id:
                return jsonify({
                    "has_new": True,
                    "type": msg['type'],
                    "text": msg['text'],
                    "id": msg['id'],
                    "tts_id": msg.get("tts_id")
                })
    return jsonify({"has_new": False, "id": last_id})


@app.route('/poll/ack', methods=['POST'])
def poll_ack():
    """确认消费队首消息"""
    with _queue_lock:
        if _msg_queue:
            _msg_queue.pop(0)
    return jsonify({"ok": True})


# ===== 主动推送接口（供飞书等远程客户端调用）=====
@app.route('/push', methods=['POST'])
def push():
    """推送用户问题或AI回答到队列，同时生成用户消息TTS"""
    data = request.get_json() or {}
    msg_type = data.get('type', 'ai')
    text = data.get('text', '')
    this_id = str(uuid.uuid4())
    if text:
        entry = {"type": msg_type, "text": text, "id": this_id}
        if msg_type == 'user':
            tts_id = str(uuid.uuid4())
            entry["tts_id"] = tts_id
            audio_file = os.path.join(tempfile.gettempdir(), f"dh_user_{uuid.uuid4().hex}.mp3")
            with _user_tts_lock:
                if _current_user_tts["file"] and os.path.exists(_current_user_tts["file"]):
                    try:
                        os.remove(_current_user_tts["file"])
                    except:
                        pass
                _current_user_tts["file"] = audio_file
                _current_user_tts["id"] = tts_id
                _current_user_tts["done"] = False

            def do_user_tts():
                async def gen():
                    await edge_tts.Communicate(
                        clean_for_tts(text),
                        "zh-CN-YunxiNeural",
                        rate="+0%", pitch="+0Hz"
                    ).save(audio_file)
                try:
                    asyncio.run(gen())
                    with _user_tts_lock:
                        if _current_user_tts["id"] == tts_id:
                            _current_user_tts["done"] = True
                    safe_print(f"[user TTS] done id={tts_id[:8]}")
                except Exception as e:
                    safe_print(f"[user TTS] error: {e}")
                    with _user_tts_lock:
                        if _current_user_tts["id"] == tts_id:
                            _current_user_tts["done"] = True
            t = __import__("threading").Thread(target=do_user_tts)
            t.daemon = True
            t.start()
        with _queue_lock:
            _msg_queue.append(entry)
    return jsonify({"ok": True, "id": this_id})


# ===== 单独生成用户TTS（供浏览器本地输入时使用）=====
@app.route('/tts_generate', methods=['POST'])
def tts_generate():
    """生成用户消息TTS，返回tts_id供轮询"""
    data = request.get_json() or {}
    text = data.get('text', '')
    if not text:
        return jsonify({"error": "empty"}), 400
    tts_id = str(uuid.uuid4())
    audio_file = os.path.join(tempfile.gettempdir(), f"dh_user_{uuid.uuid4().hex}.mp3")
    with _user_tts_lock:
        if _current_user_tts["file"] and os.path.exists(_current_user_tts["file"]):
            try: os.remove(_current_user_tts["file"])
            except: pass
        _current_user_tts["file"] = audio_file
        _current_user_tts["id"] = tts_id
        _current_user_tts["done"] = False
    def do():
        async def gen():
            await edge_tts.Communicate(clean_for_tts(text), "zh-CN-YunxiNeural", rate="+0%", pitch="+0Hz").save(audio_file)
        try:
            asyncio.run(gen())
            with _user_tts_lock:
                if _current_user_tts["id"] == tts_id:
                    _current_user_tts["done"] = True
            safe_print(f"[user TTS local] done id={tts_id[:8]}")
        except Exception as e:
            safe_print(f"[user TTS local] error: {e}")
            with _user_tts_lock:
                if _current_user_tts["id"] == tts_id:
                    _current_user_tts["done"] = True
    t = __import__("threading").Thread(target=do)
    t.daemon = True
    t.start()
    return jsonify({"tts_id": tts_id})


@app.route('/tts_status')
def tts_status():
    source = request.args.get('source', 'ai')
    if source == 'user':
        with _user_tts_lock:
            return jsonify({"ready": _current_user_tts["done"], "id": _current_user_tts["id"]})
    else:
        with _tts_lock:
            return jsonify({"ready": _current_audio["done"], "id": _current_audio["id"]})


@app.route('/tts_audio')
def tts_audio():
    source = request.args.get('source', 'ai')
    if source == 'user':
        with _user_tts_lock:
            if _current_user_tts["done"] and _current_user_tts["file"]:
                af = _current_user_tts["file"]
            else:
                af = None
    else:
        with _tts_lock:
            if _current_audio["done"] and _current_audio["file"]:
                af = _current_audio["file"]
            else:
                af = None
    if af and os.path.exists(af):
        return send_file(af, mimetype="audio/mpeg")
    resp = Response("pending", status=202)
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ===== 聊天接口（直接返回AI回答，供浏览器页面/本地调用，不入远程队列）=====
@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    if not data or not data.get("text", "").strip():
        return jsonify({"error": "empty"}), 400

    question = data.get("text", "").strip()
    session_id = data.get("session_id", "default")
    this_id = str(uuid.uuid4())
    safe_print(f"[/chat] id={this_id[:8]} session={session_id} q={question[:30]}")

    with history_lock:
        history = chat_history[session_id]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": question}]

    base_url = _config.get("base_url", "")
    api_key = _config.get("api_key", "")
    model = _config.get("model", "")
    if not base_url or not api_key or not model:
        return jsonify({"error": "请先在设置中配置 API 地址、Key 和模型"}), 400

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 200,
    }
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
            verify=False,
        )
        result = resp.json()
        reply_text = result["choices"][0]["message"]["content"]
        safe_print(f"[/chat] reply: {reply_text[:50]}")
    except Exception as e:
        import traceback
        err_msg = f"[/chat] AI error: {e}\n{traceback.format_exc()}"
        safe_print(err_msg)
        return jsonify({"error": "AI调用失败，请重试"}), 500

    # 更新历史并持久化
    with history_lock:
        chat_history[session_id].append({"role": "user", "content": question})
        chat_history[session_id].append({"role": "assistant", "content": reply_text})
        if len(chat_history[session_id]) > 20:
            chat_history[session_id] = chat_history[session_id][-20:]
    # 异步保存，不阻塞响应
    import threading
    threading.Thread(target=_save_history, daemon=True).start()

    # 生成 TTS（只生成AI回答TTS，用户消息TTS在/push处理）
    audio_file = os.path.join(tempfile.gettempdir(), f"dh_{uuid.uuid4().hex}.mp3")
    reply_for_tts = clean_for_tts(reply_text)

    with _tts_lock:
        if _current_audio["file"] and os.path.exists(_current_audio["file"]):
            try:
                os.remove(_current_audio["file"])
            except:
                pass
        _current_audio["file"] = audio_file
        _current_audio["id"] = this_id
        _current_audio["done"] = False

    def background_tts():
        async def gen():
            await edge_tts.Communicate(reply_for_tts, "zh-CN-XiaoxiaoNeural", rate="+0%", pitch="+0Hz").save(audio_file)
        try:
            asyncio.run(gen())
            with _tts_lock:
                if _current_audio["id"] == this_id:
                    _current_audio["done"] = True
            safe_print(f"[TTS] done id={this_id[:8]}")
        except Exception as e:
            safe_print(f"[TTS] error: {e}")
            with _tts_lock:
                if _current_audio["id"] == this_id:
                    _current_audio["done"] = True

    t = threading.Thread(target=background_tts)
    t.daemon = True
    t.start()

    return jsonify({"text": reply_text})


@app.route('/reset', methods=['POST'])
def reset_chat():
    data = request.get_json() or {}
    session_id = data.get("session_id", "default")
    with history_lock:
        if session_id in chat_history:
            del chat_history[session_id]
    _save_history()
    return jsonify({"ok": True})


if __name__ == "__main__":
    safe_print("=" * 30)
    safe_print(" 数字主持人 服务")
    safe_print(f" 端口: {PORT}")
    safe_print("=" * 30)
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
    finally:
        _save_history()