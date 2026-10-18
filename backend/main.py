"""Little Reader – parent-owned AI children's story platform.
Run behind the provided Nginx gateway; all private routes require a parent token.
"""
from __future__ import annotations
import base64, io, json, os, secrets, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from pydantic import BaseModel, Field, EmailStr
from pwdlib import PasswordHash
from PIL import Image, UnidentifiedImageError
import jwt, httpx

DB = os.getenv('DATABASE_URL', 'sqlite:////data/little_reader.db')
SECRET = os.getenv('JWT_SECRET','')
KEY = os.getenv('OPENAI_API_KEY','')
MEDIA = Path(os.getenv('MEDIA_DIR','/data/media'))
MEDIA.mkdir(parents=True,exist_ok=True)
if DB.startswith('sqlite:'): Path(DB.split('///')[-1]).parent.mkdir(parents=True,exist_ok=True)
engine=create_engine(DB, connect_args={'check_same_thread':False} if DB.startswith('sqlite:') else {},pool_pre_ping=True)
SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base()

class User(Base):
    __tablename__='users'
    id=Column(String,primary_key=True,default=lambda:str(uuid.uuid4()))
    email=Column(String,unique=True,index=True,nullable=False)
    password_hash=Column(String,nullable=False)
    created_at=Column(DateTime,default=datetime.utcnow)
class Child(Base):
    __tablename__='children'
    id=Column(String,primary_key=True,default=lambda:str(uuid.uuid4()))
    user_id=Column(String,ForeignKey('users.id'),nullable=False,index=True)
    name=Column(String(60),nullable=False)
    age=Column(Integer,nullable=False)
    interests=Column(Text,default='[]')
    reading_level=Column(Integer,default=1)
    language=Column(String(5),default='ar')
    avatar_path=Column(String,nullable=True)
    created_at=Column(DateTime,default=datetime.utcnow)
class Story(Base):
    __tablename__='stories'
    id=Column(String,primary_key=True,default=lambda:str(uuid.uuid4()))
    user_id=Column(String,ForeignKey('users.id'),nullable=False,index=True)
    child_id=Column(String,ForeignKey('children.id'),nullable=False,index=True)
    title=Column(String(255),nullable=False)
    content=Column(Text,nullable=False)
    cover_path=Column(String,nullable=True)
    character_path=Column(String,nullable=True)
    genre=Column(String(80),default='adventure')
    skill=Column(String(80),default='friendship')
    created_at=Column(DateTime,default=datetime.utcnow)
class Reading(Base):
    __tablename__='readings'
    id=Column(String,primary_key=True,default=lambda:str(uuid.uuid4()))
    user_id=Column(String,ForeignKey('users.id'),nullable=False,index=True)
    child_id=Column(String,ForeignKey('children.id'),nullable=False,index=True)
    story_id=Column(String,ForeignKey('stories.id'),nullable=False,index=True)
    pages_read=Column(Integer,default=0)
    quiz_correct=Column(Integer,default=0)
    quiz_total=Column(Integer,default=0)
    minutes=Column(Integer,default=0)
    created_at=Column(DateTime,default=datetime.utcnow)
Base.metadata.create_all(engine)
app=FastAPI(title='Little Reader API',version='1.0.0',docs_url='/api/docs',openapi_url='/api/openapi.json')
origins=[x.strip() for x in os.getenv('CORS_ORIGINS','http://localhost:8080').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST','PATCH','DELETE'],allow_headers=['Authorization','Content-Type'])
password_hash=PasswordHash.recommended()
bearer=HTTPBearer(auto_error=False)

def db_session():
    with SessionLocal() as db: yield db

def current_user(token:Optional[HTTPAuthorizationCredentials]=Depends(bearer),db:Session=Depends(db_session)):
    if not token or not SECRET:raise HTTPException(401,'Please sign in')
    try: uid=jwt.decode(token.credentials,SECRET,algorithms=['HS256'],options={'require':['exp','sub']})['sub']
    except (jwt.InvalidTokenError,KeyError):raise HTTPException(401,'Invalid or expired session')
    user=db.get(User,uid)
    if not user:raise HTTPException(401,'Account unavailable')
    return user

def owned_child(db,user,child_id):
    child=db.get(Child,child_id)
    if not child or child.user_id!=user.id:raise HTTPException(404,'Child not found')
    return child

def owned_story(db,user,story_id):
    story=db.get(Story,story_id)
    if not story or story.user_id!=user.id:raise HTTPException(404,'Story not found')
    return story

def issue_token(user):
    return jwt.encode({'sub':user.id,'exp':datetime.now(timezone.utc)+timedelta(days=7)},SECRET,algorithm='HS256')
class Credentials(BaseModel):
    email:EmailStr
    password:str=Field(min_length=10,max_length=128)
class ChildCreate(BaseModel):
    name:str=Field(min_length=1,max_length=60)
    age:int=Field(ge=3,le=12)
    interests:list[str]=Field(default_factory=list,max_length=8)
    language:str=Field(default='ar',pattern='^(ar|en)$')
class ChildUpdate(BaseModel):
    name:Optional[str]=Field(default=None,min_length=1,max_length=60)
    age:Optional[int]=Field(default=None,ge=3,le=12)
    reading_level:Optional[int]=Field(default=None,ge=1,le=4)
    interests:Optional[list[str]]=None
class StoryRequest(BaseModel):
    child_id:str
    genre:str=Field(default='adventure',max_length=80)
    value:str=Field(default='friendship',max_length=80)
    setting:str=Field(default='magical forest',max_length=100)
    pages:int=Field(default=5,ge=3,le=8)
    image_style:str=Field(default='warm 2D watercolor picture book',max_length=120)
    illustrate:bool=True
class SessionCreate(BaseModel):
    child_id:str
    story_id:str
    pages_read:int=Field(ge=0,le=50)
    minutes:int=Field(default=0,ge=0,le=240)
    answers:list[int]=Field(default_factory=list,max_length=20)

@app.get('/api/health')
def health():return {'status':'ok','openai_configured':bool(KEY),'auth_configured':bool(SECRET)}
@app.post('/api/auth/register')
def register(data:Credentials,db:Session=Depends(db_session)):
    if not SECRET:raise HTTPException(503,'JWT_SECRET missing')
    email=str(data.email).lower()
    if db.query(User).filter_by(email=email).first():raise HTTPException(409,'Email already registered')
    u=User(email=email,password_hash=password_hash.hash(data.password));db.add(u);db.commit();db.refresh(u)
    return {'token':issue_token(u),'user':{'id':u.id,'email':u.email}}
@app.post('/api/auth/login')
def login(data:Credentials,db:Session=Depends(db_session)):
    if not SECRET:raise HTTPException(503,'JWT_SECRET missing')
    u=db.query(User).filter_by(email=str(data.email).lower()).first()
    if not u or not password_hash.verify(data.password,u.password_hash):raise HTTPException(401,'Invalid email or password')
    return {'token':issue_token(u),'user':{'id':u.id,'email':u.email}}
@app.get('/api/me')
def me(u:User=Depends(current_user)):return {'id':u.id,'email':u.email}

def child_json(c):return {'id':c.id,'name':c.name,'age':c.age,'interests':json.loads(c.interests or '[]'),'language':c.language,'reading_level':c.reading_level,'avatar_url':f'/api/media/{c.avatar_path}' if c.avatar_path else None}
@app.get('/api/children')
def children(u:User=Depends(current_user),db:Session=Depends(db_session)):
    return [child_json(c) for c in db.query(Child).filter_by(user_id=u.id).all()]
@app.post('/api/children')
def create_child(data:ChildCreate,u:User=Depends(current_user),db:Session=Depends(db_session)):
    c=Child(user_id=u.id,name=data.name,age=data.age,interests=json.dumps(data.interests[:8],ensure_ascii=False),language=data.language)
    db.add(c);db.commit();db.refresh(c);return child_json(c)
@app.patch('/api/children/{child_id}')
def edit_child(child_id:str,data:ChildUpdate,u:User=Depends(current_user),db:Session=Depends(db_session)):
    c=owned_child(db,u,child_id)
    for k,v in data.model_dump(exclude_none=True).items():setattr(c,k,json.dumps(v,ensure_ascii=False) if k=='interests' else v)
    db.commit();return child_json(c)
@app.delete('/api/children/{child_id}')
def delete_child(child_id:str,u:User=Depends(current_user),db:Session=Depends(db_session)):
    c=owned_child(db,u,child_id)
    story_ids=[s.id for s in db.query(Story).filter_by(user_id=u.id,child_id=c.id).all()]
    db.query(Reading).filter_by(child_id=c.id,user_id=u.id).delete()
    for s in db.query(Story).filter_by(child_id=c.id,user_id=u.id).all():
        for path in (s.cover_path,s.character_path):
            if path:(MEDIA/path).unlink(missing_ok=True)
        db.delete(s)
    if c.avatar_path:(MEDIA/c.avatar_path).unlink(missing_ok=True)
    db.delete(c);db.commit();return {'deleted':True}

async def openai_json(system:str,prompt:str):
    if not KEY:raise HTTPException(503,'OPENAI_API_KEY missing')
    async with httpx.AsyncClient(timeout=100) as client:
        try:
            r=await client.post('https://api.openai.com/v1/chat/completions',headers={'Authorization':f'Bearer {KEY}'},json={'model':os.getenv('OPENAI_TEXT_MODEL','gpt-4.1-mini'),'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],'response_format':{'type':'json_object'}})
            r.raise_for_status();return json.loads(r.json()['choices'][0]['message']['content'])
        except (httpx.HTTPError,ValueError,KeyError) as e:raise HTTPException(502,'Story generation failed; check model access and OpenAI configuration') from e

async def image_call(prompt:str,reference:Optional[bytes]=None):
    if not KEY:raise HTTPException(503,'OPENAI_API_KEY missing')
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            if reference:
                r=await client.post('https://api.openai.com/v1/images/edits',headers={'Authorization':f'Bearer {KEY}'},files={'image':('reference.png',reference,'image/png')},data={'model':os.getenv('OPENAI_IMAGE_MODEL','gpt-image-1.5'),'prompt':prompt,'size':'1024x1024'})
            else:
                r=await client.post('https://api.openai.com/v1/images/generations',headers={'Authorization':f'Bearer {KEY}'},json={'model':os.getenv('OPENAI_IMAGE_MODEL','gpt-image-1.5'),'prompt':prompt,'size':'1024x1024'})
            r.raise_for_status();return base64.b64decode(r.json()['data'][0]['b64_json'])
        except (httpx.HTTPError,KeyError,IndexError) as e:raise HTTPException(502,'Image generation failed; check image model access') from e

def persist_image(blob:bytes):
    filename=secrets.token_hex(18)+'.png';(MEDIA/filename).write_bytes(blob);return filename

@app.post('/api/children/{child_id}/avatar')
async def avatar(child_id:str,photo:UploadFile=File(...),consent:bool=Form(...),u:User=Depends(current_user),db:Session=Depends(db_session)):
    if not consent:raise HTTPException(400,'Parent consent required')
    c=owned_child(db,u,child_id)
    if photo.content_type not in ('image/png','image/jpeg','image/webp'):raise HTTPException(415,'PNG, JPEG, or WebP only')
    content=await photo.read(5_000_001)
    if len(content)>5_000_000:raise HTTPException(413,'Photo too large (max 5MB)')
    try:
        img=Image.open(io.BytesIO(content));img.verify()
        img=Image.open(io.BytesIO(content)).convert('RGB');img.thumbnail((1024,1024))
        buffer=io.BytesIO();img.save(buffer,format='PNG');data=buffer.getvalue()
    except (UnidentifiedImageError,OSError,ValueError):raise HTTPException(400,'Unreadable photo')
    result=await image_call('Transform this child into an age-appropriate, joyful 2D watercolor picture-book cartoon character. Preserve distinguishing hairstyle, complexion, face shape and clothing colors. Full body, cheerful neutral illustrated backdrop, non-photorealistic. Do not include written words.',data)
    new_path=persist_image(result)
    if c.avatar_path:(MEDIA/c.avatar_path).unlink(missing_ok=True)
    c.avatar_path=new_path;db.commit()
    return {'avatar_url':f'/api/media/{new_path}','child':child_json(c)}

@app.post('/api/stories')
async def generate_story(req:StoryRequest,u:User=Depends(current_user),db:Session=Depends(db_session)):
    c=owned_child(db,u,req.child_id)
    prompt=f'''Create an ORIGINAL, safe, gentle, age appropriate children's story in {'Modern Standard Arabic' if c.language=='ar' else 'English'}. Main character is a child named {c.name}, age {c.age}; interests: {json.loads(c.interests)}. Genre {req.genre}; setting {req.setting}; value {req.value}; reading level {c.reading_level} on 1-4 scale. Exactly {req.pages} pages, each 1-3 short sentences. Child protagonist must make a meaningful choice on page 3, with two safe alternatives; incorporate selected example of kindness or courage. Return JSON with title, pages:[{{text,scene,choice?:{{question,options:[string,string],responses:[string,string]}}}}], quiz:[{{question,options:[string,string,string],correct_index:integer}}] (3 questions). Scene is a visual illustration prompt without real-life personal details. No violence, horror, adult themes or unsafe behavior. No JSON markdown.''' 
    result=await openai_json('You are a professional Arabic/English children’s educator and picture-book author. Respond valid JSON only; prioritize safety, comprehension, warm storytelling.',prompt)
    pages=result.get('pages')
    if not isinstance(pages,list) or len(pages)!=req.pages or not all(isinstance(x,dict) and isinstance(x.get('text'),str) for x in pages):raise HTTPException(502,'Invalid LLM page schema')
    quiz=result.get('quiz',[])
    if not isinstance(quiz,list):quiz=[]
    cover_path=None
    if req.illustrate:
        scene=str(pages[0].get('scene','storybook magical illustration'))[:500]
        visual=f'{req.image_style}; whimsical, colorful, fully clothed child protagonist, age {c.age}, not photorealistic, no text. Scene: {scene}'
        ref=(MEDIA/c.avatar_path).read_bytes() if c.avatar_path else None
        cover_path=persist_image(await image_call('Keep this EXACT same illustrated child character when composing the scene. '+visual if ref else visual,ref))
    s=Story(user_id=u.id,child_id=c.id,title=str(result.get('title','حكايتي'))[:255],content=json.dumps({'pages':pages,'quiz':quiz},ensure_ascii=False),cover_path=cover_path,character_path=c.avatar_path,genre=req.genre,skill=req.value)
    db.add(s);db.commit();db.refresh(s);return story_json(s)

def story_json(s):
    d=json.loads(s.content)
    return {'id':s.id,'child_id':s.child_id,'title':s.title,'pages':d['pages'],'quiz':d.get('quiz',[]),'cover_url':f'/api/media/{s.cover_path}' if s.cover_path else None,'avatar_url':f'/api/media/{s.character_path}' if s.character_path else None,'genre':s.genre,'value':s.skill,'created_at':s.created_at.isoformat()}
@app.get('/api/stories')
def list_stories(child_id:Optional[str]=None,u:User=Depends(current_user),db:Session=Depends(db_session)):
    q=db.query(Story).filter_by(user_id=u.id)
    if child_id:owned_child(db,u,child_id);q=q.filter_by(child_id=child_id)
    return [story_json(s) for s in q.order_by(Story.created_at.desc()).limit(100)]
@app.get('/api/stories/{story_id}')
def get_story(story_id:str,u:User=Depends(current_user),db:Session=Depends(db_session)):return story_json(owned_story(db,u,story_id))
@app.delete('/api/stories/{story_id}')
def delete_story(story_id:str,u:User=Depends(current_user),db:Session=Depends(db_session)):
    s=owned_story(db,u,story_id);db.query(Reading).filter_by(story_id=s.id,user_id=u.id).delete();db.delete(s);db.commit();return {'deleted':True}
@app.post('/api/reading-sessions')
def save_reading(data:SessionCreate,u:User=Depends(current_user),db:Session=Depends(db_session)):
    owned_child(db,u,data.child_id);story=owned_story(db,u,data.story_id)
    if story.child_id!=data.child_id:raise HTTPException(400,'Story does not belong to this child')
    d=json.loads(story.content);answers=d.get('quiz',[])
    correct=sum(1 for i,x in enumerate(data.answers[:len(answers)]) if isinstance(answers[i],dict) and x==answers[i].get('correct_index'))
    r=Reading(user_id=u.id,child_id=data.child_id,story_id=story.id,pages_read=min(data.pages_read,len(d['pages'])),minutes=data.minutes,quiz_correct=correct,quiz_total=len(answers));db.add(r);db.commit()
    return {'correct':correct,'total':len(answers),'recommendation':'Try a slightly harder story next time' if correct>=2 else 'Try another story at the current level'}
@app.get('/api/progress/{child_id}')
def progress(child_id:str,u:User=Depends(current_user),db:Session=Depends(db_session)):
    c=owned_child(db,u,child_id)
    sessions=db.query(Reading).filter_by(user_id=u.id,child_id=c.id).all()
    total=sum(x.quiz_total for x in sessions);right=sum(x.quiz_correct for x in sessions)
    return {'child':child_json(c),'sessions':len(sessions),'stories_completed':len(set(x.story_id for x in sessions if x.pages_read>0)),'minutes':sum(x.minutes for x in sessions),'quiz_accuracy':round(100*right/total) if total else None,'recommendation':'More comprehension challenges' if total and right/total>=0.8 else 'Short sentences and guided reading'}
@app.get('/api/media/{filename}')
def media(filename:str,u:User=Depends(current_user),db:Session=Depends(db_session)):
    if '/' in filename or '\\' in filename or not filename.endswith('.png'):raise HTTPException(404,'Not found')
    user_files=set()
    for c in db.query(Child).filter_by(user_id=u.id).all():
        if c.avatar_path:user_files.add(c.avatar_path)
    for s in db.query(Story).filter_by(user_id=u.id).all():
        if s.cover_path:user_files.add(s.cover_path)
        if s.character_path:user_files.add(s.character_path)
    if filename not in user_files or not (MEDIA/filename).is_file():raise HTTPException(404,'Not found')
    return FileResponse(MEDIA/filename,media_type='image/png',headers={'Cache-Control':'private, max-age=60'})