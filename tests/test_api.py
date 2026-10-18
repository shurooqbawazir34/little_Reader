import os, tempfile
os.environ['DATABASE_URL']='sqlite:///'+tempfile.mkdtemp()+'/test.db'
os.environ['MEDIA_DIR']=tempfile.mkdtemp()
os.environ['JWT_SECRET']='test-secret-should-never-be-used-in-production'
from fastapi.testclient import TestClient
from backend.main import app
client=TestClient(app)
def test_health():
    r=client.get('/api/health');assert r.status_code==200;assert r.json()['status']=='ok'
def test_auth_and_parent_isolation():
    r=client.post('/api/auth/register',json={'email':'parent@example.com','password':'secure-secret-123'})
    assert r.status_code==200,r.text
    t=r.json()['token']; h={'Authorization':'Bearer '+t}
    c=client.post('/api/children',json={'name':'سارة','age':7,'interests':['الفضاء'],'language':'ar'},headers=h)
    assert c.status_code==200,c.text
    child_id=c.json()['id']
    assert len(client.get('/api/children',headers=h).json())==1
    other=client.post('/api/auth/register',json={'email':'second@example.com','password':'secure-secret-234'}).json()['token']
    h2={'Authorization':'Bearer '+other}
    assert client.patch('/api/children/'+child_id,json={'name':'not mine'},headers=h2).status_code==404
    assert client.get('/api/progress/'+child_id,headers=h2).status_code==404
    assert client.delete('/api/children/'+child_id,headers=h).status_code==200
    assert client.get('/api/children',headers=h).json()==[]