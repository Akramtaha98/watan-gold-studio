#!/usr/bin/env python3
"""Standard-library-only, deterministic WaRiSAN dashboard demonstration server.

Run: python3 mock_qwen.py
This is a mock, not Qwen inference or a DBP/security assessment. Tokens are Unicode
code points (including whitespace), NOT Qwen tokens. Encoding is stateless and
exactly reversible across threads, requests and server restarts. No corpus or
request bodies are logged. The dashboard source is never imported or modified.
"""
import copy
import json
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HOST, PORT = '127.0.0.1', 8000
MODELS = ('qwen3-14b', 'Qwen/Qwen3-8B', 'Qwen/Qwen3-32B')
CATEGORIES = ('DBP_Grammar', 'WaRiSAN_Identity', 'Safety_Refusal', 'General_BM_QA')
MAX_BODY_BYTES = 4 * 1024 * 1024


def encode(text):
    if not isinstance(text, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise ValueError('prompt must be a valid Unicode string')
    return [ord(char) for char in text]


def decode(tokens):
    if not isinstance(tokens, list) or any(type(t) is not int or not 0 <= t <= 0x10FFFF or 0xD800 <= t <= 0xDFFF for t in tokens):
        raise ValueError('tokens must be Unicode scalar integer IDs')
    return ''.join(chr(token) for token in tokens)


def extract_record(payload):
    """Prefer the dashboard's serialized corpus_record, never its policy prompt."""
    turns = payload.get('messages')
    if not isinstance(turns, list):
        raise ValueError('messages must contain the dashboard verification request')
    for turn in reversed(turns):
        if not isinstance(turn, dict) or turn.get('role') != 'user':
            continue
        content = turn.get('content')
        if not isinstance(content, str):
            continue
        try:
            obj = json.loads(content)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            if isinstance(obj.get('corpus_record'), dict):
                return obj['corpus_record']
            if any(k in obj for k in ('turns', 'messages', 'text')):
                return obj
    raise ValueError('No JSON corpus_record found in the verification request')


def verification(record):
    """Return every field in the dashboard's strict RESULT_SCHEMA, no extras."""
    if not isinstance(record, dict):
        raise ValueError('corpus_record must be an object')
    meta = record.get('metadata', record.get('meta', {}))
    if not isinstance(meta, dict):
        raise ValueError('metadata must be an object')
    messages = copy.deepcopy(record.get('messages') or record.get('turns') or [])
    benchmark = bool(record.get('eval_tag')) or 'turns' in record
    if 'turns' in record and messages and messages[-1].get('role') == 'user':
        answer = record.get('reference_answer')
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Evaluation turns require a reference_answer')
        messages.append({'role': 'assistant', 'content': answer})
    dialogue = record.get('kind') == 'dialogue' or bool(messages)
    if dialogue:
        offset = int(bool(messages and isinstance(messages[0], dict) and messages[0].get('role') == 'system'))
        if len(messages) - offset < 2 or (len(messages) - offset) % 2:
            raise ValueError('Dialogue must have complete user/assistant pairs')
        for i, turn in enumerate(messages):
            expected = 'system' if offset and i == 0 else 'user' if (i-offset) % 2 == 0 else 'assistant'
            if not isinstance(turn, dict) or set(turn) != {'role', 'content'} or turn['role'] != expected or not isinstance(turn['content'], str) or not turn['content'].strip():
                raise ValueError('Invalid dialogue role alternation or content')
        text = ''
    else:
        text = record.get('text', '')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Prose requires nonempty text')
    original = '\n'.join(t['content'] for t in messages) if dialogue else text
    # Dashboard ingestion adds doc_title from the filename even for raw prose.
    # Ignore that recognizable generated default; explicit title/section wins.
    title = meta.get('doc_title') or record.get('doc_title')
    section = meta.get('section') or record.get('section')
    source = str(meta.get('source', ''))
    reference = bool(section or title and str(title) != Path(source).stem)
    reference = reference or bool(re.search(r'\b(pekeliling|tatacara|peraturan|polisi|garis panduan|circular|guidelines?)\b', original, re.I))
    stage = 'GOLD_4' if dialogue and benchmark else 'GOLD_3' if dialogue else 'GOLD_2' if reference else 'GOLD_1'
    desired = record.get('requested_stage', 'AUTO')
    if desired != 'AUTO':
        allowed = {'GOLD_3', 'GOLD_4'} if dialogue else {'GOLD_1', 'GOLD_2'}
        if desired not in allowed:
            raise ValueError('Requested tier is incompatible with this source structure')
        stage = desired
    category = meta.get('category', record.get('category'))
    if category not in CATEGORIES:
        category = 'WaRiSAN_Identity' if re.search(r'warisan|gemma4', original, re.I) else 'DBP_Grammar' if re.search(r'\b(ejaan|tatabahasa|betulkan|diatas)\b', original, re.I) else 'General_BM_QA'
    result = dict(confidence=0.98, dbp_score=0.98, dbp_errors=[], policy_errors=[], factual_errors=[], unsafe=False, corrupted=False, language='ms', lang_near_malay=False, three_r=[], damaged_chars=False, explicit_content=False, dialect_flag=bool(meta.get('dialect_flag', False)), script=meta.get('script', 'rumi'), spelling_variant='original', reason='MOCK: clean demonstration record; no AI assessment was performed.', messages=messages, text=text, stage=stage, category=category, domain='Bahasa Melayu')
    reasons = []

    def edit(content):
        if re.search(r'\bdiatas\b', content, re.I):
            if not result['dbp_errors']:
                result['dbp_errors'].append('Demo correction: write the preposition as di atas.')
            content = re.sub(r'\bdiatas\b', lambda m: 'DI ATAS' if m[0].isupper() else 'Di atas' if m[0][0].isupper() else 'di atas', content, flags=re.I)
        if re.search(r'\bGemma4-31B\b', content, re.I):
            if not result['policy_errors']:
                result['policy_errors'].append('Demo legacy checkpoint label; confirm the owner-approved model identity.')
            content = re.sub(r'\bGemma4-31B\b', 'google/gemma-4-31B-it', content, flags=re.I)
        return content

    if dialogue:
        for turn in messages:
            if turn['role'] == 'assistant':
                turn['content'] = edit(turn['content'])
    else:
        result['text'] = edit(text)
        if result['text'] != text:
            result['spelling_variant'] = 'normalised'
    if result['dbp_errors'] or result['policy_errors']:
        reasons.append('proposed spelling/checkpoint edit requires human review')
    lang = str(meta.get('language', meta.get('lang', ''))).lower()
    if lang in {'id', 'indonesian', 'indonesia', 'lang_near_malay'} or record.get('lang_near_malay') is True or re.search(r'\b(karena|silakan|nggak|bahasa indonesia|tidak bisa)\b', original, re.I):
        result.update(language='id', lang_near_malay=True)
        reasons.append('retain near-Malay for linguist review')
    for flag, pattern in [('Race', r'\b(kaum|perkauman|rasisme|racial)\b'), ('Religion', r'\b(agama|islam|hindu|buddha|kristian|religion)\b'), ('Royalty', r'\b(raja|sultan|diraja|royalty)\b')]:
        if re.search(pattern, original, re.I):
            result['three_r'].append(flag)
    if result['three_r']:
        reasons.append('neutral 3R keyword demo; review rather than automatic rejection')
    if result['script'] not in {'rumi', 'jawi'}:
        result['script'] = 'rumi'
    if re.search(r'[\u0600-\u06ff]', original):
        result.update(script='jawi', language='jawi')
    if '\ufffd' in original or '\x00' in original:
        result['damaged_chars'] = True
        reasons.append('damaged character marker')
    if reasons:
        result['confidence'] = 0.85
        result['dbp_score'] = 0.85 if result['dbp_errors'] else 0.98
        result['reason'] = 'MOCK: ' + '; '.join(reasons) + '.'
    return result


class MockHandler(BaseHTTPRequestHandler):
    server_version = 'WaRiSANMockQwen/1.0'

    def log_message(self, fmt, *args):
        # Never log incoming records, policy text or credentials.
        pass

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-WaRiSAN-Mock', 'true')
        self.end_headers()
        self.wfile.write(data)

    def error(self, status, message):
        self.send_json(status, {'error': {'message': message, 'type': 'mock_request_error', 'code': status}})

    def do_GET(self):
        if urlsplit(self.path).path.rstrip('/') != '/v1/models':
            return self.error(404, 'Use GET /v1/models')
        self.send_json(200, {'object':'list', 'data':[{'id':m, 'object':'model', 'created':0, 'owned_by':'LOCAL-MOCK-NOT-REAL-QWEN'} for m in MODELS], 'mock':True})

    def do_POST(self):
        path = urlsplit(self.path).path.rstrip('/')
        if path not in {'/tokenize', '/detokenize', '/v1/chat/completions'}:
            return self.error(404, 'Unknown endpoint')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY_BYTES:
                return self.error(413, 'JSON request must be between 1 byte and 4 MiB')
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('JSON body must be an object')
            model = payload.get('model', MODELS[0])
            if model not in MODELS:
                return self.error(404, 'Choose Qwen/Qwen3-8B or Qwen/Qwen3-32B')
            if path == '/tokenize':
                tokens = encode(payload.get('prompt'))
                return self.send_json(200, {'tokens':tokens, 'count':len(tokens), 'max_model_len':32768, 'mock':True})
            if path == '/detokenize':
                return self.send_json(200, {'prompt':decode(payload.get('tokens')), 'mock':True})
            if payload.get('stream'):
                return self.error(400, 'Mock supports non-streaming verification only')
            result = verification(extract_record(payload))
            self.send_json(200, {'id':'chatcmpl-mock-'+uuid.uuid4().hex, 'object':'chat.completion', 'created':int(time.time()), 'model':model, 'choices':[{'index':0, 'message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False,allow_nan=False)}, 'finish_reason':'stop'}], 'mock':True})
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            self.error(400, 'Invalid JSON, token IDs or verification record; inspect the request structure')
        except (BrokenPipeError, ConnectionResetError):
            pass

    def setup(self):
        super().setup()
        self.connection.settimeout(30)


def main():
    try:
        server = ThreadingHTTPServer((HOST, PORT), MockHandler)
    except OSError as exc:
        print(f'Cannot start mock at {HOST}:{PORT}: {exc}\nPort 8000 must be free. Stop the service using it before retrying.', file=sys.stderr)
        return 1
    print('''
=== WaRiSAN LOCAL MOCK QWEN — DEMO ONLY ===
API Base URL:   http://127.0.0.1:8000/v1
Served Model:   Qwen/Qwen3-8B (also Qwen/Qwen3-32B)

1. Leave this terminal running.
2. Start the dashboard: streamlit run watan_gold_studio.py
3. Keep the default API URL/model and click Test Connection in Streamlit.
4. Upload your sample file, then Run Qwen 3 Verification.

No H200, extra packages or API key required for this mock server.
Responses are scripted rules, NOT AI/DBP verification.
Token IDs are Unicode code points, NOT genuine Qwen/vLLM tokens.
The server listens only on 127.0.0.1 and does not log record bodies.
Press Ctrl+C to stop.
''', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nMock stopped.', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
