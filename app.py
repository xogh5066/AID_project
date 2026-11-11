"""Hosted demo; run with gunicorn, never commit secrets."""
import os,time,secrets,threading
from pathlib import Path
from flask import Flask,request,session,jsonify,redirect
from core import validate
from ensemble import settings,evaluate_ensemble
app=Flask(__name__)
app.secret_key=os.environ.get('SESSION_SECRET') or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=120000,SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax')
lock=threading.Lock(); counts={}; busy=threading.BoundedSemaphore(3)
LOGIN='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>BUCHEON AID</title><body style="font-family:system-ui;background:#f3f6f8;padding:10%;color:#172b36"><h1>AID Data Lab</h1><p>심사·수업용 입장 코드를 입력하세요. 개인정보는 입력하지 마세요.</p><form method="post" action="/login"><input type="password" name="code" required maxlength="200" placeholder="입장 코드" aria-label="입장 코드"><button>실습 시작</button></form></body></html>'''
def limited(key,limit):
    day=int(time.time()//86400)
    with lock:
        for k in list(counts):
            if k[0]!=day: del counts[k]
        k=(day,key);n=counts.get(k,0)
        if n>=limit:return True
        counts[k]=n+1
        return False
@app.after_request
def headers(r):
    r.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Strict-Transport-Security':'max-age=31536000'})
    return r
@app.get('/health')
def health():return {'ok':True}
@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        # Deliberately shared cap; forwarded client IP is not trusted.
        if limited('login',300):return '입장 요청 한도 초과. 관리자에게 문의하세요.',429
        code=os.environ.get('ACCESS_CODE','')
        if len(code)<12:return '관리자가 12자 이상 ACCESS_CODE를 설정해야 합니다.',503
        if secrets.compare_digest(request.form.get('code',''),code):
            session.clear();session['auth']=True;session['csrf']=secrets.token_urlsafe(32);session['id']=secrets.token_urlsafe(16)
            return redirect('/')
        return '입장 코드가 올바르지 않습니다.',403
    return LOGIN
@app.get('/')
def index():
    if not session.get('auth'):return redirect('/login')
    return (Path(__file__).parent/'index.html').read_text(encoding='utf-8').replace('__CSRF_TOKEN__',session['csrf'])
@app.get('/api/status')
def status():
    if not session.get('auth'):return jsonify(error='다시 입장하세요.'),401
    try:
        settings();return {'configured':True}
    except Exception:return {'configured':False}
@app.post('/api/feedback')
def feedback():
    if not session.get('auth'):return jsonify(error='다시 입장하세요.'),401
    if not secrets.compare_digest(request.headers.get('X-AID-Token',''),session.get('csrf','!')):return jsonify(error='페이지를 새로고침하세요.'),403
    if not request.is_json:return jsonify(error='JSON 요청만 허용합니다.'),415
    try:p=validate(request.get_json())
    except (ValueError,TypeError):return jsonify(error='세 답안과 모든 분석 결과를 확인하세요.'),400
    if not busy.acquire(blocking=False):return jsonify(error='요청이 많습니다. 잠시 후 시도하세요.'),429
    try:
        if limited('session:'+session['id'],10) or limited('global',int(os.environ.get('DAILY_AI_LIMIT','50'))):return jsonify(error='오늘의 AI 호출 한도에 도달했습니다.'),429
        return jsonify(feedback=evaluate_ensemble(p))
    except ValueError as e:return jsonify(error=str(e)),400
    except Exception:return jsonify(error='처리 실패. 관리자에게 문의하세요.'),500
    finally:busy.release()
@app.errorhandler(413)
def large(e):return jsonify(error='요청 크기가 너무 큽니다.'),413
